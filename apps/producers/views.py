from django.db import models, transaction
from django.db.models import Count, Q, Sum
from rest_framework import generics, filters, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from .models import Agent, Producer
from .serializers import AgentSerializer, AgentCreateSerializer, ProducerSerializer, ProducerCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, IsAgentOrAbove, resolve_cooperative, scope_to_cooperative, is_owner
from utils.field_id import generate_field_id_base, get_next_producer_index


class AgentListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['cooperative', 'is_active']
    search_fields = ['user__full_name', 'code', 'zone']

    def get_serializer_class(self):
        # Création → crée aussi le compte de connexion de l'agent
        return AgentCreateSerializer if self.request.method == 'POST' else AgentSerializer

    def get_permissions(self):
        # La coopérative (ou l'admin) peut enregistrer ses propres agents ;
        # l'agent peut lire sa propre fiche (le frontend en a besoin pour « Mes parcelles »)
        if self.request.method == 'GET':
            return [IsAgentOrAbove()]
        return [IsCooperativeOrAdmin()]

    def get_queryset(self):
        # ordre stable : indispensable pour paginer sans sauter ni dupliquer de lignes
        qs = (Agent.objects.select_related('user', 'cooperative')
              .annotate(parcel_count_annot=Count('parcels'), total_hectares_annot=Sum('parcels__area_hectares'))
              .order_by('created_at', 'id'))
        user = self.request.user
        if user.role == 'agent':
            return qs.filter(user=user)
        # propriétaire → tous ; super admin → agents de ses coopératives ; coopérative → les siens
        return scope_to_cooperative(qs, user)


class AgentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AgentSerializer
    permission_classes = [IsCooperativeOrAdmin]

    def get_queryset(self):
        # une coopérative ne peut agir que sur SES agents
        return scope_to_cooperative(Agent.objects.select_related('user', 'cooperative'), self.request.user)

    def perform_update(self, serializer):
        # la coopérative d'un agent ne peut pas être changée par une coopérative
        # seul le propriétaire peut déplacer un agent vers une autre coopérative
        if not is_owner(self.request.user):
            serializer.validated_data.pop('cooperative', None)
        serializer.save()

    def perform_destroy(self, instance):
        # le compte de connexion de l'agent est supprimé avec lui ; ses parcelles mappées sont conservées
        from apps.accounts.models import ActivityLog
        user = instance.user
        ActivityLog.objects.create(user=self.request.user, action='delete_agent', resource='agent', resource_id=str(instance.id),
                                   details=f'{user.full_name if user else ""} ({instance.code})', ip_address=self.request.META.get('REMOTE_ADDR'))
        instance.delete()
        if user is not None and user.role == 'agent':
            user.delete()


class ProducerListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['cooperative', 'assigned_agent', 'section', 'village', 'gender', 'is_active']
    search_fields = ['first_name', 'last_name', 'field_id_base', 'village', 'phone']
    ordering_fields = ['last_name', 'created_at', 'section']

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method == 'POST' else ProducerSerializer

    def get_permissions(self):
        return [IsAgentOrAbove()]

    def get_queryset(self):
        from django.db.models import IntegerField, OuterRef, Subquery, Value
        from django.db.models.functions import Coalesce
        from apps.parcels.models import LegacyParcel
        legacy = (LegacyParcel.objects.filter(producer=OuterRef('pk')).order_by().values('producer')
                  .annotate(n=Count('id')).values('n'))
        qs = (Producer.objects.select_related('cooperative', 'assigned_agent__user')
              .annotate(parcel_count_annot=Count('parcels'), total_hectares_annot=Sum('parcels__area_hectares'),
                        legacy_count_annot=Coalesce(Subquery(legacy, output_field=IntegerField()), Value(0)))
              .order_by('last_name', 'first_name', 'id'))
        # ?to_map=true : producteurs sans aucun polygone (registre sans correspondance) → à cartographier par les agents
        tm = self.request.query_params.get('to_map')
        if tm in ('true', '1'):
            qs = qs.filter(parcel_count_annot=0, legacy_count_annot=0)
        elif tm in ('false', '0'):
            qs = qs.exclude(parcel_count_annot=0, legacy_count_annot=0)
        # Coopérative et agent : tous les producteurs de leur coopérative ; super admin : ses coopératives
        return scope_to_cooperative(qs, self.request.user)


class ProducerDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAgentOrAbove]

    def get_queryset(self):
        return scope_to_cooperative(
            Producer.objects.select_related('cooperative', 'assigned_agent__user'), self.request.user)

    def get_permissions(self):
        # suppression réservée à la coopérative et à l'admin
        if self.request.method == 'DELETE':
            return [IsCooperativeOrAdmin()]
        return [IsAgentOrAbove()]

    def get_serializer_class(self):
        return ProducerCreateSerializer if self.request.method in ('PUT', 'PATCH') else ProducerSerializer

    def perform_update(self, serializer):
        old = serializer.instance.field_id_base
        p = serializer.save()
        if p.field_id_base != old:          # code corrigé : les polygones sont recroisés
            from .matching import relink
            relink(p.cooperative)

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        if instance.parcels.exists():
            raise ValidationError({'detail': 'Ce producteur a des parcelles mappées : supprimez-les d\'abord (Parcelles).'})
        if instance.lot_lines.exists():
            raise ValidationError({'detail': 'Ce producteur figure dans une fiche de lot : il ne peut pas être supprimé.'})
        instance.delete()


class ProducerBulkImportView(APIView):
    """
    POST /api/v1/producers/bulk/
    Corps : {"cooperative": "<uuid, super admin seulement>", "producers": [ {...}, ... ]}

    Crée en une requête les producteurs d'un fichier Excel. Chaque ligne peut porter
    `extra_data` (toutes les colonnes du fichier, sous leur entête d'origine). Aucun champ n'est obligatoire.

    Conçu pour des milliers de lignes : le nombre de requêtes SQL ne dépend PAS du nombre de lignes
    (la base Neon est à plusieurs centaines de ms du serveur : une requête par ligne dépasserait le délai).
    Les valeurs invalides d'un champ typé (année, superficie…) sont ignorées ; elles restent dans extra_data.
    Réponse : {"created": [...producteurs...], "errors": [{"index": i, "errors": {...}}]}
    """
    permission_classes = [IsCooperativeOrAdmin]
    MAX_ROWS = 5000

    TEXT_FIELDS = [
        'first_name', 'last_name', 'phone', 'national_id', 'village', 'district', 'region', 'section',
        'country', 'national_farm_id', 'owner_first_name', 'owner_last_name', 'owner_phone',
        'owner_national_id', 'inspector_name',
    ]
    INT_FIELDS = [
        'birth_year', 'num_units', 'certification_year', 'permanent_workers', 'temporary_workers',
        'inspection_year', 'inspection_month', 'inspection_day',
    ]

    @staticmethod
    def _int(v):
        try:
            n = int(float(str(v).replace(',', '.').strip()))
        except (TypeError, ValueError):
            return None
        return n if 0 <= n <= 32767 else None

    @staticmethod
    def _decimal(v):
        try:
            n = round(float(str(v).replace(',', '.').replace(' ', '')), 2)
        except (TypeError, ValueError):
            return None
        return n if 0 <= n < 10 ** 8 else None

    def _clean(self, row, limits):
        data = {}
        for f in self.TEXT_FIELDS:
            v = row.get(f)
            if v is not None and v != '':
                data[f] = str(v).strip()[:limits[f]]
        for f in self.INT_FIELDS:
            n = self._int(row.get(f)) if row.get(f) not in (None, '') else None
            if n is not None:
                data[f] = n
        if row.get('total_area_ha') not in (None, ''):
            n = self._decimal(row['total_area_ha'])
            if n is not None:
                data['total_area_ha'] = n
        data['gender'] = 'F' if str(row.get('gender', '')).upper().startswith('F') else 'M'
        if row.get('owner_gender') in ('M', 'F'):
            data['owner_gender'] = row['owner_gender']
        data['farm_type'] = 'large' if row.get('farm_type') == 'large' else 'small'
        extra = row.get('extra_data')
        data['extra_data'] = extra if isinstance(extra, dict) else {}
        return data

    def post(self, request):
        rows = request.data.get('producers') or []
        if not isinstance(rows, list) or not rows:
            return Response({'detail': 'Aucun producteur à importer.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(rows) > self.MAX_ROWS:
            return Response({'detail': f'Maximum {self.MAX_ROWS} producteurs par envoi.'},
                            status=status.HTTP_400_BAD_REQUEST)

        cooperative = resolve_cooperative(request, request.data)
        if cooperative is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)

        limits = {f.name: f.max_length for f in Producer._meta.get_fields()
                  if isinstance(f, models.CharField) and f.max_length}
        # Un agent qui importe est rattaché d'office à ses producteurs
        agent = getattr(request.user, 'agent_profile', None) if request.user.role == 'agent' else None

        # use_codes : le fichier a une colonne « code producteur » → aucune ligne sans code n'est acceptée
        use_codes = bool(request.data.get('use_codes'))
        valid, errors, raw_codes = [], [], []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append({'index': i, 'errors': {'detail': 'Ligne invalide.'}})
                continue
            code = str(row.get('code') or '').strip()[:50]
            if use_codes and not code:
                errors.append({'index': i, 'errors': {'code': 'Code producteur absent : ligne non importée.'}})
                continue
            valid.append(self._clean(row, limits))
            raw_codes.append(code)

        created, updated = [], []
        if valid:
            from .matching import normalize_code
            with transaction.atomic():
                # 1) Lignes avec le code du registre : ce code EST l'identifiant du producteur (jamais régénéré).
                #    Un code déjà connu dans la coopérative met à jour le producteur au lieu d'en créer un doublon.
                coded = [(d, raw_codes[k]) for k, d in enumerate(valid) if raw_codes[k]]
                existing = {p.field_id_base: p for p in Producer.objects.filter(
                    cooperative=cooperative, field_id_base__in=[c for _, c in coded])}
                seen = set()
                objs = []
                for data, code in coded:
                    if code in seen:
                        errors.append({'index': None, 'errors': {'code': f'Code {code} en double dans le fichier : seule la 1re ligne est prise.'}})
                        continue
                    seen.add(code)
                    p = existing.get(code)
                    if p is not None:
                        extra = {**(p.extra_data or {}), **data.pop('extra_data', {})}
                        for k, v in data.items():
                            setattr(p, k, v)
                        p.extra_data = extra
                        updated.append(p)
                    else:
                        objs.append(Producer(cooperative=cooperative, assigned_agent=agent, field_id_base=code,
                                             match_key=normalize_code(code) or '', **data))
                if updated:
                    Producer.objects.bulk_update(updated, list({k for d, _ in coded for k in d} | {'extra_data'}), batch_size=500)

                # 2) Fichier sans colonne « code » : identifiant généré comme avant (ancien comportement)
                uncoded = [d for k, d in enumerate(valid) if not raw_codes[k]]
                if uncoded and not use_codes:
                    sections = {d.get('section', '') for d in uncoded}
                    codes = {sec: generate_field_id_base(sec, 0)[:-6] for sec in sections}
                    counts = dict(
                        Producer.objects.filter(cooperative=cooperative, section__in=sections)
                        .values_list('section').annotate(n=Count('id')).values_list('section', 'n'))
                    prefix_q = Q()
                    for code in set(codes.values()):
                        prefix_q |= Q(field_id_base__startswith=code)
                    used = set(Producer.objects.filter(prefix_q).values_list('field_id_base', flat=True))
                    next_index = {sec: counts.get(sec, 0) + 1 for sec in sections}
                    for data in uncoded:
                        section = data.get('section', '')
                        base = generate_field_id_base(section, next_index[section])
                        while base in used:
                            next_index[section] += 1
                            base = generate_field_id_base(section, next_index[section])
                        used.add(base)
                        next_index[section] += 1
                        objs.append(Producer(cooperative=cooperative, assigned_agent=agent, field_id_base=base,
                                             match_key=normalize_code(base) or '', **data))
                created = Producer.objects.bulk_create(objs, batch_size=1000)

            # producteurs du registre ↔ anciens polygones (code)
            match = None
            if request.data.get('relink', True):
                from .matching import relink
                match = relink(cooperative, request.data.get('code_field') or None)

            try:
                from apps.accounts.notify import notify_cooperative
                notify_cooperative(
                    cooperative, ntype='info',
                    title=f'Import du registre — {len(created)} nouveau(x), {len(updated)} mis à jour',
                    message=f'{len(errors)} ligne(s) rejetée(s)' if errors else 'Toutes les lignes ont été enregistrées.',
                )
            except Exception:
                pass

        # Réponse sans requête supplémentaire : les nouveaux producteurs n'ont encore aucune parcelle
        for p in created:
            p.parcel_count_annot = 0
            p.total_hectares_annot = 0
        for p in updated:
            p.parcel_count_annot = None
            p.total_hectares_annot = None
        return Response({
            'created': ProducerSerializer(created, many=True).data,
            'updated': len(updated),
            'errors': errors,
            'match': match if valid else None,
        }, status=status.HTTP_201_CREATED if (created or updated) else status.HTTP_400_BAD_REQUEST)


class ProducerMatchView(APIView):
    """
    GET  /api/v1/producers/match/?cooperative=   état du croisement registre ↔ polygones + attributs disponibles
    POST /api/v1/producers/match/  {"code_field": "<attribut des polygones>" | null}   recalcule les liens
    """
    permission_classes = [IsCooperativeOrAdmin]

    def _coop(self, request):
        return resolve_cooperative(request, request.data if request.method == 'POST' else None)

    def get(self, request):
        from apps.parcels.models import LegacyParcel
        from .matching import detect_code_field, stats
        coop = self._coop(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        fields = set()
        for props in LegacyParcel.objects.filter(cooperative=coop).values_list('properties', flat=True)[:500]:
            fields.update((props or {}).keys())
        best, score = detect_code_field(coop)
        return Response({**stats(coop), 'fields': sorted(fields), 'suggested_field': best, 'suggested_matches': score})

    def post(self, request):
        from .matching import relink
        coop = self._coop(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(relink(coop, request.data.get('code_field') or None))


class ProducerBulkDeleteView(APIView):
    """
    POST /api/v1/producers/bulk-delete/ {"ids": [...]}
    Supprime des producteurs de la coopérative. Protégés (non supprimés) : ceux qui ont des parcelles mappées
    par les agents (elles seraient supprimées avec eux) ou qui figurent dans une fiche de lot.
    Leurs anciens polygones ne sont pas supprimés : ils redeviennent « sans producteur ».
    """
    permission_classes = [IsCooperativeOrAdmin]

    def post(self, request):
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'detail': 'Aucun producteur sélectionné.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 20000:
            return Response({'detail': 'Maximum 20 000 producteurs par suppression.'}, status=status.HTTP_400_BAD_REQUEST)
        qs = scope_to_cooperative(Producer.objects.filter(id__in=ids), request.user)
        protected = qs.filter(Q(parcels__isnull=False) | Q(lot_lines__isnull=False)).distinct()
        kept = list(protected.values_list('field_id_base', flat=True)[:50])
        n_kept = protected.count()
        deletable = qs.exclude(id__in=protected.values('id'))
        coops = set(deletable.values_list('cooperative_id', flat=True))
        deleted = deletable.count()
        with transaction.atomic():
            deletable.delete()
        from apps.accounts.models import ActivityLog
        ActivityLog.objects.create(user=request.user, action='delete_producers', resource='producer',
                                   details=f'{deleted} supprimé(s), {n_kept} protégé(s)', ip_address=request.META.get('REMOTE_ADDR'))
        return Response({'deleted': deleted, 'protected': n_kept, 'protected_codes': kept, 'cooperatives': [str(c) for c in coops]})


class ProducerRecodeView(APIView):
    """
    Recodage des producteurs existants avec le code de leur registre (colonne conservée dans extra_data).
    GET  /api/v1/producers/recode/?cooperative=     colonnes candidates, colonne suggérée, aperçu
    POST /api/v1/producers/recode/ {"column": "...", "apply": true}   applique (sinon simulation) puis recroise les polygones
    """
    permission_classes = [IsCooperativeOrAdmin]
    HINTS = ('identifiant interne unique', 'code producteur', 'code planteur', 'code du producteur', 'matricule', 'id producteur')

    @staticmethod
    def _value(v):
        if v is None:
            return ''
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()[:50]

    def _plan(self, coop, column):
        producers = list(Producer.objects.filter(cooperative=coop).only('id', 'field_id_base', 'extra_data'))
        new_codes, seen, dup, missing = {}, set(), [], 0
        for p in producers:
            code = self._value((p.extra_data or {}).get(column))
            if not code:
                missing += 1
                continue
            if code in seen:
                dup.append(code)
                continue
            seen.add(code)
            new_codes[p.id] = code
        # codes déjà pris par des producteurs qui ne sont pas recodés
        kept = {p.field_id_base for p in producers if p.id not in new_codes}
        conflicts = [c for c in new_codes.values() if c in kept]
        for pid in [k for k, c in new_codes.items() if c in kept]:
            new_codes.pop(pid)
        changes = sum(1 for p in producers if p.id in new_codes and new_codes[p.id] != p.field_id_base)
        return producers, new_codes, {'producers': len(producers), 'recoded': changes, 'unchanged': len(new_codes) - changes,
                                      'without_code': missing, 'duplicates': len(dup), 'duplicate_codes': dup[:20],
                                      'conflicts': len(conflicts), 'sample': [{'avant': p.field_id_base, 'apres': new_codes[p.id]} for p in producers if p.id in new_codes][:8]}

    def get(self, request):
        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        counts = {}
        for extra in Producer.objects.filter(cooperative=coop).values_list('extra_data', flat=True)[:3000]:
            for k, v in (extra or {}).items():
                if v not in (None, ''):
                    counts[k] = counts.get(k, 0) + 1
        from apps.producers.matching import normalize_code
        def score(k):
            n = k.lower()
            return (any(h in n for h in self.HINTS), counts[k])
        suggested = max(counts, key=score) if counts and any(any(h in k.lower() for h in self.HINTS) for k in counts) else None
        return Response({'columns': [{'name': k, 'filled': n} for k, n in sorted(counts.items(), key=lambda x: -x[1])],
                         'suggested': suggested,
                         'preview': self._plan(coop, suggested)[2] if suggested else None})

    def post(self, request):
        coop = resolve_cooperative(request, request.data)
        column = request.data.get('column')
        if coop is None or not column:
            return Response({'detail': 'Coopérative et colonne requises.'}, status=status.HTTP_400_BAD_REQUEST)
        producers, new_codes, report = self._plan(coop, column)
        if not request.data.get('apply'):
            return Response({**report, 'applied': False})
        from .matching import normalize_code, relink
        changed = []
        for p in producers:
            c = new_codes.get(p.id)
            if c and c != p.field_id_base:
                p.field_id_base, p.match_key = c, normalize_code(c) or ''
                changed.append(p)
        with transaction.atomic():
            # en deux temps : évite une collision passagère sur la contrainte d'unicité (échange de codes)
            for p in changed:
                Producer.objects.filter(pk=p.pk).update(field_id_base=f'__tmp__{p.pk.hex[:20]}')
            Producer.objects.bulk_update(changed, ['field_id_base', 'match_key'], batch_size=500)
        from apps.accounts.models import ActivityLog
        ActivityLog.objects.create(user=request.user, action='recode_producers', resource='producer',
                                   details=f'{len(changed)} recodé(s) depuis « {column} »', ip_address=request.META.get('REMOTE_ADDR'))
        return Response({**report, 'applied': True, 'match': relink(coop)})

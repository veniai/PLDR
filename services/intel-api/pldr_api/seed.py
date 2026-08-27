from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Assessment, Claim, Document, Entity, Event, EventDocument, EventEntity, Evidence, Snapshot, Source


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def digest(text: str) -> str:
    return sha256(' '.join(text.split()).encode('utf-8')).hexdigest()


SOURCES = [
    ('src_reuters','Reuters','wire','reuters','healthy',1),
    ('src_reuters_reprint','Reuters Maritime Reprint','aggregator','reuters','error',4),
    ('src_ap','Associated Press','wire','ap','healthy',1),
    ('src_ap_east','AP Syndication East','aggregator','ap','healthy',4),
    ('src_ap_eu','AP Syndication Europe','aggregator','ap','stale',4),
    ('src_sca','Suez Canal Authority','official','sca','healthy',1),
    ('src_imo','International Maritime Organization','official','imo','healthy',1),
    ('src_nasa','NASA Earth Observatory','official','nasa','healthy',1),
    ('src_unctad','UN Trade and Development','official','unctad','healthy',1),
    ('src_bbc','BBC News','media','bbc','healthy',2),
    ('src_aj','Al Jazeera','media','aljazeera','healthy',2),
    ('src_bsm','Bernhard Schulte Shipmanagement','company','bsm','healthy',2),
    ('src_boskalis','Boskalis','company','boskalis','healthy',2),
    ('src_evergreen','Evergreen Marine','company','evergreen','healthy',2),
    ('src_maersk','Maersk Operations Advisory','company','maersk','healthy',2),
    ('src_rotterdam','Port of Rotterdam','official','rotterdam','healthy',2),
]

EVENTS = [
    ('evt_grounding','Ever Given 在苏伊士运河搁浅','超大型集装箱船在运河南段搁浅，双向通航中断。','incident','2021-03-23T06:00:00Z','Suez Canal, Egypt',30.0,32.55,'critical',0.98),
    ('evt_queue','等待船舶数量快速上升','运河两端等待船舶持续增加，航运网络出现明显拥堵。','disruption','2021-03-24T08:00:00Z','Suez Canal approaches',30.2,32.45,'high',0.95),
    ('evt_salvage','多方展开拖带与疏浚救援','拖轮、挖泥船和专业救援团队持续尝试让船体重新浮起。','response','2021-03-25T10:00:00Z','Suez Canal, Egypt',30.01,32.56,'high',0.94),
    ('evt_reroute','部分船舶考虑绕行好望角','部分承运人评估或执行绕行，以降低等待时间的不确定性。','adaptation','2021-03-26T12:00:00Z','Cape of Good Hope',-34.35,18.47,'medium',0.87),
    ('evt_refloat','Ever Given 成功重新浮起','船舶在拖轮和疏浚作业后重新浮起，运河开始恢复通航。','resolution','2021-03-29T13:00:00Z','Suez Canal, Egypt',30.01,32.56,'critical',0.99),
    ('evt_recovery','积压船舶逐步疏解','运河恢复后，排队船舶需要数日才能逐步通过。','recovery','2021-03-30T08:00:00Z','Suez Canal, Egypt',30.25,32.5,'high',0.91),
    ('evt_detention','船舶被扣留并进入赔偿谈判','事故后船舶在埃及水域被扣留，相关方围绕赔偿和责任进行谈判。','legal','2021-04-13T08:00:00Z','Great Bitter Lake, Egypt',30.34,32.48,'medium',0.88),
    ('evt_release','达成和解后船舶获准离开','各方达成和解后，船舶完成释放程序并离开相关水域。','legal','2021-07-07T10:00:00Z','Ismailia, Egypt',30.59,32.27,'medium',0.9),
]


def counts(session: Session) -> dict[str, int]:
    return {
        'sources': session.scalar(select(func.count()).select_from(Source)) or 0,
        'documents': session.scalar(select(func.count()).select_from(Document)) or 0,
        'events': session.scalar(select(func.count()).select_from(Event)) or 0,
        'claims': session.scalar(select(func.count()).select_from(Claim)) or 0,
        'evidence': session.scalar(select(func.count()).select_from(Evidence)) or 0,
    }


def reset(session: Session) -> None:
    for model in [Evidence, Claim, Assessment, EventEntity, EventDocument, Snapshot, Document, Entity, Event, Source]:
        session.execute(delete(model))
    session.commit()


def seed_database(session: Session, force: bool = False) -> dict[str, int]:
    if not force and (session.scalar(select(func.count()).select_from(Event)) or 0) > 0:
        return counts(session)
    reset(session)
    now = datetime.now(timezone.utc)
    sources = {}
    for idx, (sid, name, stype, group, status, tier) in enumerate(SOURCES):
        source = Source(
            id=sid, name=name, base_url=f'https://demo.pldr.local/{sid}', country='International', language='en',
            source_type=stype, reliability_tier=tier, independence_group=group, status=status,
            last_success_at=now if status == 'healthy' else dt('2021-07-07T12:00:00Z'),
            last_error='Demo source timeout: cached snapshot retained' if status == 'error' else None,
        )
        session.add(source); sources[sid] = source
    session.flush()

    for event_idx, (eid,title,summary,etype,start,location,lat,lon,importance,confidence) in enumerate(EVENTS):
        event = Event(id=eid,title=title,summary=summary,event_type=etype,start_at=dt(start),end_at=None,latitude=lat,longitude=lon,location_name=location,importance=importance,status='confirmed',confidence=confidence)
        session.add(event)
        entity_a = Entity(id=f'ent_{event_idx}_a',name=['Ever Given','Suez Canal Authority','Salvage teams','Carriers','Ever Given','Shipping queue','Ever Given','Ever Given'][event_idx],entity_type='organization' if event_idx not in {0,4,6,7} else 'vessel',aliases=[])
        entity_b = Entity(id=f'ent_{event_idx}_b',name=['Suez Canal Authority','Waiting vessels','Suez Canal Authority','Global carriers','Suez Canal Authority','Suez Canal Authority','Egyptian authorities','Suez Canal Authority'][event_idx],entity_type='organization',aliases=[])
        session.add_all([entity_a,entity_b]); session.flush()
        session.add_all([EventEntity(event_id=eid,entity_id=entity_a.id,role='primary'),EventEntity(event_id=eid,entity_id=entity_b.id,role='related')])

        doc_objs=[]
        for j in range(6):
            source = sources[SOURCES[(event_idx*2+j) % len(SOURCES)][0]]
            fact1=f'{title} was reported as a confirmed development in the curated P0 demonstration.'
            fact2='Independent-source grouping for this event is tracked separately from document count.'
            fact3=f'Open questions remain about secondary effects and exact attribution for event {event_idx+1}.'
            body=f'{fact1} {fact2} {fact3} Source context {j+1} records the timeline and preserves a deterministic evidence substring for validation.'
            doc_id=f'doc_{event_idx}_{j}'
            doc=Document(id=doc_id,source_id=source.id,canonical_url=f'https://demo.pldr.local/{eid}/{j+1}',title=f'{title} source record {j+1}',body=body,published_at=dt(start),fetched_at=dt(start),language='en',content_hash=digest(body),upstream_story_id=f'{eid}-story-{j//2}',is_cached=True,metadata_json={'demo':True,'topic':'Suez Canal blockage 2021'})
            session.add(doc); session.flush(); doc_objs.append(doc)
            session.add(Snapshot(id=f'snap_{event_idx}_{j}',document_id=doc.id,captured_at=dt(start),content_hash=doc.content_hash,excerpt=body,storage_path='inline-demo'))
            session.add(EventDocument(event_id=eid,document_id=doc.id,relevance=1.0))

        claims=[
            Claim(id=f'clm_{event_idx}_0',event_id=eid,text=f'{title} is supported by multiple curated records.',status='supported',confidence=0.94,origin='machine',temporal_scope=start[:10]),
            Claim(id=f'clm_{event_idx}_1',event_id=eid,text='Document count should not be interpreted as independent-source count.',status='contested' if event_idx%2==0 else 'supported',confidence=0.78,origin='machine',temporal_scope=start[:10]),
            Claim(id=f'clm_{event_idx}_2',event_id=eid,text='Secondary effects and precise attribution remain partially unresolved.',status='unverified',confidence=0.62,origin='machine',temporal_scope=start[:10]),
        ]
        session.add_all(claims); session.flush()
        snippets=[
            f'{title} was reported as a confirmed development in the curated P0 demonstration.',
            'Independent-source grouping for this event is tracked separately from document count.',
            f'Open questions remain about secondary effects and exact attribution for event {event_idx+1}.',
        ]
        evidence_plan=[(0,0,0,'supports'),(0,1,0,'supports'),(0,2,0,'supports'),(1,2,1,'supports'),(1,3,1,'contradicts' if event_idx%2==0 else 'supports'),(2,4,2,'context'),(2,5,2,'context')]
        for k,(claim_idx,doc_idx,snip_idx,stance) in enumerate(evidence_plan):
            snippet=snippets[snip_idx]; body=doc_objs[doc_idx].body; start_offset=body.index(snippet)
            session.add(Evidence(id=f'evd_{event_idx}_{k}',claim_id=claims[claim_idx].id,document_id=doc_objs[doc_idx].id,snippet=snippet,start_offset=start_offset,end_offset=start_offset+len(snippet),stance=stance,strength=0.82 if stance=='supports' else 0.65,note='Exact-substring demo evidence.'))
        session.add(Assessment(id=f'asm_{event_idx}',event_id=eid,judgement=f'{title} can be treated as the highest-confidence temporary judgement for P0 demonstration purposes.',assumptions=['Curated records preserve the intended event chronology.'],alternatives=['Some secondary effects may have different causal explanations.'],information_gaps=[f'Original-source refresh for event {event_idx+1}', 'Independent corroboration beyond the curated demo pack'],falsifiers=['Fresh primary evidence contradicts the event chronology.'],confidence=0.82,generated_by='human-curated-demo',generated_at=dt(start)))
    session.commit()
    return counts(session)

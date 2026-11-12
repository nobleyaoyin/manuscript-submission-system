"""Prospective formatting and explicitly local prototype publication."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import guard_demo_path
from app.database import BASE_DIR, get_db
from app.models import (User, Manuscript, FinalEditingSubmission, FinalEditingSubmissionInvitation,
                        FormatterInvitation, FormattingSubmission, PrototypePublication)
from app.routers.users import require_role

router = APIRouter(prefix='/formatting', tags=['Formatting and prototype publication'])

class Invite(BaseModel):
    manuscript_id: int = Field(gt=0)
    formatter_id: int = Field(gt=0)
class Response(BaseModel):
    decision: Literal['accept', 'decline']
class Review(BaseModel):
    decision: Literal['approve', 'request_revision']
    comments: str = Field(min_length=1, max_length=5000)
    @field_validator('comments')
    @classmethod
    def nonblank(cls, value):
        value=value.strip()
        if not value: raise ValueError('Comments cannot be blank')
        return value

def latest_final(db, mid):
    return db.scalar(select(FinalEditingSubmission).join(FinalEditingSubmissionInvitation).where(FinalEditingSubmission.manuscript_id==mid).order_by(FinalEditingSubmission.submission_id.desc()).limit(1))
def ready_final(s):
    return s is not None and s.status=='author_approved' and s.author_reviewed_at is not None and s.eic_approved_by is not None and s.eic_reviewed_at is not None

def file_path(relative, folder):
    root=guard_demo_path(BASE_DIR / 'uploads' / folder).resolve()
    path=guard_demo_path(BASE_DIR / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower()!='.pdf' or not path.is_file(): raise HTTPException(404,'File unavailable')
    return path

def verified_file(s):
    p=file_path(s.file_path,'formatting')
    if hashlib.sha256(p.read_bytes()).hexdigest()!=s.sha256: raise HTTPException(409,'Formatted file integrity check failed')
    return p

def get_invitation(db, iid):
    i=db.get(FormatterInvitation,iid)
    if not i: raise HTTPException(404,'Invitation not found')
    return i

def info(db,m):
    invitations=db.scalars(select(FormatterInvitation).where(FormatterInvitation.manuscript_id==m.manuscript_id).order_by(FormatterInvitation.invitation_id.desc())).all()
    subs=db.scalars(select(FormattingSubmission).where(FormattingSubmission.manuscript_id==m.manuscript_id).order_by(FormattingSubmission.submission_id.desc())).all()
    pub=db.get(PrototypePublication,m.manuscript_id)
    return {'manuscript_id':m.manuscript_id,'title':m.title,'status':m.status,
      'invitations':[{'invitation_id':i.invitation_id,'formatter_id':i.formatter_id,'formatter':db.get(User,i.formatter_id).name,'status':i.status} for i in invitations],
      'submissions':[{'submission_id':s.submission_id,'status':s.status,'comments':s.comments,'author_comments':s.author_comments,'eic_comments':s.eic_comments} for s in subs],
      'publication':{'submission_id':pub.submission_id,'published_at':pub.published_at,'published_by':pub.published_by,'doi_status':pub.doi_status} if pub else None}

@router.get('/formatters')
def formatters(user:User=Depends(require_role('eic')),db:Session=Depends(get_db)):
    return [{'user_id':u.user_id,'name':u.name} for u in db.scalars(select(User).where(User.role=='formatter').order_by(User.name)).all()]

@router.get('/manuscripts/{mid}')
def manuscript_state(mid:int,user:User=Depends(require_role('eic','author','formatter')),db:Session=Depends(get_db)):
    m=db.get(Manuscript,mid)
    if not m: raise HTTPException(404,'Manuscript not found')
    if user.role=='author' and m.author_id!=user.user_id: raise HTTPException(403,'Not your manuscript')
    if user.role=='formatter' and not db.scalar(select(FormatterInvitation).where(FormatterInvitation.manuscript_id==mid,FormatterInvitation.formatter_id==user.user_id)):
        raise HTTPException(403,'No formatter invitation')
    result=info(db,m)
    if user.role=='formatter':
        result['invitations']=[i for i in result['invitations'] if i['formatter_id']==user.user_id]
        if not any(i['status']=='accepted' for i in result['invitations']): result['submissions']=[]
    return result

@router.get('/my')
def my_work(user:User=Depends(require_role('formatter','author')),db:Session=Depends(get_db)):
    if user.role=='formatter':
        ids=db.scalars(select(FormatterInvitation.manuscript_id).where(FormatterInvitation.formatter_id==user.user_id).distinct()).all()
    else:
        ids=db.scalars(select(FormatterInvitation.manuscript_id).join(Manuscript).where(Manuscript.author_id==user.user_id).distinct()).all()
    return [manuscript_state(mid,user,db) for mid in ids]

@router.post('/invite',status_code=201)
def invite(data:Invite,user:User=Depends(require_role('eic')),db:Session=Depends(get_db)):
    m=db.get(Manuscript,data.manuscript_id); person=db.get(User,data.formatter_id)
    if not m: raise HTTPException(404,'Manuscript not found')
    if not person or person.role!='formatter': raise HTTPException(400,'Select an eligible formatter')
    final=latest_final(db,m.manuscript_id)
    if m.status not in ('final_editing','formatting') or not ready_final(final): raise HTTPException(409,'EIC-approved and author-confirmed final edit required')
    i=FormatterInvitation(manuscript_id=m.manuscript_id,formatter_id=person.user_id,invited_by=user.user_id,final_submission_id=final.submission_id,status='pending')
    db.add(i);m.status='formatting'
    try: db.commit();db.refresh(i)
    except IntegrityError: db.rollback();raise HTTPException(409,'A formatter invitation is already active')
    return {'invitation_id':i.invitation_id}

@router.post('/invitations/{iid}/respond')
def respond(iid:int,data:Response,user:User=Depends(require_role('formatter')),db:Session=Depends(get_db)):
    i=get_invitation(db,iid)
    if i.formatter_id!=user.user_id: raise HTTPException(403,'Only the invited formatter may respond')
    m=db.get(Manuscript,i.manuscript_id)
    if m.status!='formatting' or not ready_final(latest_final(db,m.manuscript_id)) or latest_final(db,m.manuscript_id).submission_id!=i.final_submission_id: raise HTTPException(409,'Final edit no longer available')
    changed=db.execute(update(FormatterInvitation).where(FormatterInvitation.invitation_id==iid,FormatterInvitation.status=='pending').values(status='accepted' if data.decision=='accept' else 'declined',responded_at=datetime.now(timezone.utc)))
    if changed.rowcount!=1: raise HTTPException(409,'Invitation already answered')
    db.commit();return {'status':data.decision}

def accepted(db,iid,user):
    i=get_invitation(db,iid)
    if i.formatter_id!=user.user_id or i.status!='accepted': raise HTTPException(403,'Accepted formatter assignment required')
    m=db.get(Manuscript,i.manuscript_id); final=latest_final(db,m.manuscript_id)
    if m.status not in ('formatting','published') or not ready_final(final) or final.submission_id!=i.final_submission_id: raise HTTPException(409,'Final edit unavailable')
    return i,m,final

@router.get('/invitations/{iid}/input')
def input_pdf(iid:int,user:User=Depends(require_role('formatter')),db:Session=Depends(get_db)):
    i,m,final=accepted(db,iid,user)
    return FileResponse(file_path(final.file_path,'final_editing'),media_type='application/pdf',filename=f'final-edit-{final.submission_id}.pdf')

@router.post('/invitations/{iid}/submit',status_code=201)
def submit(iid:int,file:UploadFile=File(...),comments:str=Form(...),user:User=Depends(require_role('formatter')),db:Session=Depends(get_db)):
    i,m,_=accepted(db,iid,user)
    if m.status!='formatting': raise HTTPException(409,'Manuscript is no longer in formatting')
    comments=comments.strip()
    if not comments or len(comments)>5000: raise HTTPException(400,'Comments required')
    if file.content_type!='application/pdf': raise HTTPException(400,'Only PDF is supported')
    content=file.file.read(10*1024*1024+1)
    if len(content)>10*1024*1024: raise HTTPException(413,'File exceeds 10 MB')
    if not content.startswith(b'%PDF-'): raise HTTPException(400,'Invalid PDF')
    directory=guard_demo_path(BASE_DIR/'uploads'/'formatting'/str(m.manuscript_id));directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid4().hex+'.pdf')
    try:
        with path.open('xb') as f: f.write(content)
        s=FormattingSubmission(manuscript_id=m.manuscript_id,invitation_id=i.invitation_id,file_path=str(path.relative_to(BASE_DIR)),sha256=hashlib.sha256(content).hexdigest(),status='pending_author',comments=comments)
        db.add(s);db.commit();db.refresh(s)
    except IntegrityError:
        db.rollback();path.unlink(missing_ok=True);raise HTTPException(409,'A formatted version is already awaiting review or approved')
    except Exception:
        db.rollback();path.unlink(missing_ok=True);raise
    return {'submission_id':s.submission_id,'status':s.status}

@router.get('/submissions/{sid}/download')
def download(sid:int,user:User=Depends(require_role('author','eic','formatter')),db:Session=Depends(get_db)):
    s=db.get(FormattingSubmission,sid)
    if not s: raise HTTPException(404,'Submission not found')
    m=db.get(Manuscript,s.manuscript_id);i=get_invitation(db,s.invitation_id)
    if not (user.role=='eic' or (user.role=='author' and m.author_id==user.user_id) or (user.role=='formatter' and i.formatter_id==user.user_id and i.status=='accepted')): raise HTTPException(403,'Not authorized')
    return FileResponse(verified_file(s),media_type='application/pdf',filename=f'formatted-{sid}.pdf')

def review(sid,data,user,db,role):
    s=db.get(FormattingSubmission,sid)
    if not s: raise HTTPException(404,'Submission not found')
    m=db.get(Manuscript,s.manuscript_id)
    if role=='author' and m.author_id!=user.user_id: raise HTTPException(403,'Only the author may review')
    if m.status!='formatting': raise HTTPException(409,'Not in formatting')
    verified_file(s)
    expected='pending_author' if role=='author' else 'pending_eic'
    status=('pending_eic' if role=='author' else 'approved') if data.decision=='approve' else role+'_revision_requested'
    values={'status':status,role+'_comments':data.comments,role+'_reviewed_at':datetime.now(timezone.utc)}
    if role=='eic': values['eic_id']=user.user_id
    changed=db.execute(update(FormattingSubmission).where(FormattingSubmission.submission_id==sid,FormattingSubmission.status==expected).values(**values))
    if changed.rowcount!=1: raise HTTPException(409,'Submission is not awaiting this review')
    db.commit();return {'status':status}

@router.post('/submissions/{sid}/author-review')
def author_review(sid:int,data:Review,user:User=Depends(require_role('author')),db:Session=Depends(get_db)): return review(sid,data,user,db,'author')
@router.post('/submissions/{sid}/eic-review')
def eic_review(sid:int,data:Review,user:User=Depends(require_role('eic')),db:Session=Depends(get_db)): return review(sid,data,user,db,'eic')

@router.post('/submissions/{sid}/publish',status_code=201)
def publish(sid:int,user:User=Depends(require_role('eic')),db:Session=Depends(get_db)):
    s=db.get(FormattingSubmission,sid)
    if not s: raise HTTPException(404,'Submission not found')
    m=db.get(Manuscript,s.manuscript_id)
    if m.status!='formatting' or s.status!='approved' or not s.author_reviewed_at or not s.eic_reviewed_at or not s.eic_id: raise HTTPException(409,'Author and EIC approvals required')
    verified_file(s)
    db.add(PrototypePublication(manuscript_id=m.manuscript_id,submission_id=s.submission_id,published_by=user.user_id,doi_status='pending_external'));m.status='published'
    try: db.commit()
    except IntegrityError: db.rollback();raise HTTPException(409,'Already published')
    return {'status':'published','prototype':True,'doi_status':'pending_external','submission_id':sid}

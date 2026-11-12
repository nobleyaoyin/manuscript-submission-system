"""Disconnected lifecycle/invitation transaction prototype; NOT an auth boundary.

No router, scheduler, transport or startup code imports this module. The synthetic
authority is dependency-injected by trusted test code, not an authentication
mechanism for production or a defense against hostile code in the same process.
Legacy-token compatibility and all policies are experimental, not active.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
import sqlite3
import threading
import weakref
import jwt
from app.email.worker_store import _begin, _timestamp


class PolicyError(ValueError):
    """Sanitized prototype policy or stale-state failure."""


@dataclass(frozen=True)
class Lifecycle:
    state: str
    sequence: int
    generation: int


class AuthenticatedPrincipal:
    """Opaque identity handle; a bare user ID or role is not a principal."""
    __slots__ = ('__weakref__',)


_principals = weakref.WeakKeyDictionary()
_principal_lock = threading.Lock()


class SyntheticPrincipalAuthority:
    """Test-only verifier. A future trusted adapter must choose its own verifier.

    Creation of an authority is privileged dependency injection, never a
    request-controlled operation. This prototype offers no Python sandbox.
    """
    def __init__(self, key):
        if not isinstance(key, str) or len(key) < 32:
            raise PolicyError('Invalid synthetic verifier')
        self.__key = key

    def authenticate(self, token):
        try:
            claims = jwt.decode(token, self.__key, algorithms=['HS256'],
                                options={'require': ['sub', 'exp', 'gen']})
            subject, generation, expiry = claims['sub'], claims['gen'], claims['exp']
            if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal() or str(int(subject)) != subject:
                raise PolicyError('Invalid principal')
            actor = _id(int(subject))
            if type(generation) is not int or generation < 1 or type(expiry) is not int:
                raise PolicyError('Invalid principal')
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError, OverflowError):
            raise PolicyError('Invalid principal') from None
        principal = AuthenticatedPrincipal()
        with _principal_lock:
            _principals[principal] = (self, actor, generation, expiry)
        return principal


def _principal_actor(db, principal, authority):
    if type(principal) is not AuthenticatedPrincipal:
        raise PolicyError('Authenticated principal required')
    with _principal_lock:
        verified = _principals.get(principal)
    if verified is None:
        raise PolicyError('Authenticated principal required')
    issuer, actor, generation, expiry = verified
    if type(authority) is not SyntheticPrincipalAuthority or issuer is not authority:
        raise PolicyError('Untrusted principal issuer')
    if datetime.now(timezone.utc).timestamp() >= expiry:
        raise PolicyError('Principal expired')
    state = _state(db, actor)
    if state is None or state.state != 'active' or state.generation != generation:
        raise PolicyError('Principal unavailable')
    return actor


def _id(value):
    if type(value) is not int or value < 1:
        raise PolicyError('Invalid identity')
    return value


def _state(db, user):
    current = db.execute('SELECT state,sequence,auth_generation,last_event_id FROM account_lifecycle WHERE user_id=?', (user,)).fetchone()
    events = db.execute('SELECT event_id,sequence,from_state,to_state,auth_generation FROM account_lifecycle_events WHERE user_id=? ORDER BY sequence', (user,)).fetchall()
    if current is None:
        if events: raise PolicyError('Inconsistent lifecycle history')
        return None
    if len(events) != current[1]: raise PolicyError('Inconsistent lifecycle history')
    previous = None
    for index, (eid, seq, before, after, generation) in enumerate(events, 1):
        if seq != index or generation != index or before != previous or after not in ('active','disabled') or (index == 1 and after != 'active') or (index > 1 and after == before):
            raise PolicyError('Inconsistent lifecycle history')
        previous = after
    if not events or (events[-1][0], previous, events[-1][1], events[-1][4]) != (current[3], current[0], current[1], current[2]):
        raise PolicyError('Inconsistent lifecycle pointer')
    return Lifecycle(current[0], current[1], current[2])


def _role(db, user):
    row = db.execute('SELECT role FROM users WHERE user_id=?', (_id(user),)).fetchone()
    if row is None: raise PolicyError('Account unavailable')
    return row[0]


def _admin(db, actor):
    if _role(db, actor) != 'eic': raise PolicyError('Administrator unavailable')
    state = _state(db, actor)
    if state is None or state.state != 'active': raise PolicyError('Administrator unavailable')


def _stamp(): return _timestamp(datetime.now(timezone.utc))


def _write(db, operation):
    _begin(db)
    try:
        result = operation()
        db.commit()
        return result
    except Exception:
        if db.in_transaction: db.rollback()
        raise


def lifecycle_change(db, principal, target_id, action, *, authority, expected_sequence, expected_generation):
    """Commit one audited enrollment/disable/reactivation; no automatic retry."""
    _id(target_id)
    if type(expected_sequence) is not int or type(expected_generation) is not int:
        raise PolicyError('Invalid expected version')
    if action not in ('enroll','disable','reactivate'): raise PolicyError('Invalid action')
    def run():
        actor_id = _principal_actor(db, principal, authority)
        _admin(db, actor_id)
        target_role = _role(db, target_id)
        current = _state(db, target_id)
        if action == 'enroll':
            if current is not None or expected_sequence != 0 or expected_generation != 0: raise PolicyError('Stale lifecycle')
            prior, new, seq, gen, reason = None,'active',1,1,'initial_enrollment'
        else:
            if current is None or (current.sequence,current.generation) != (expected_sequence,expected_generation): raise PolicyError('Stale lifecycle')
            prior = current.state
            if (action,prior) not in (('disable','active'),('reactivate','disabled')): raise PolicyError('Invalid lifecycle transition')
            if action == 'disable' and actor_id == target_id: raise PolicyError('Self-disable prohibited')
            if action == 'disable' and target_role == 'eic':
                admins = db.execute("SELECT u.user_id FROM users u JOIN account_lifecycle l ON l.user_id=u.user_id WHERE u.role='eic' AND l.state='active'").fetchall()
                # Verify all purported active EIC histories before relying on count.
                for (uid,) in admins: _state(db, uid)
                if len(admins) <= 1: raise PolicyError('Last EIC cannot be disabled')
            new = 'disabled' if action == 'disable' else 'active'
            seq, gen = current.sequence+1, current.generation+1
            reason = 'administrative_disable' if action == 'disable' else 'administrative_reactivation'
        eid = str(uuid4())
        db.execute('INSERT INTO account_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?)', (eid,target_id,seq,prior,new,gen,actor_id,_stamp(),reason))
        if current is None:
            db.execute('INSERT INTO account_lifecycle VALUES (?,?,?,?,?)',(target_id,new,seq,gen,eid))
        else:
            changed=db.execute('UPDATE account_lifecycle SET state=?,sequence=?,auth_generation=?,last_event_id=? WHERE user_id=? AND state=? AND sequence=? AND auth_generation=?', (new,seq,gen,eid,target_id,prior,current.sequence,current.generation))
            if changed.rowcount != 1: raise PolicyError('Stale lifecycle')
        return Lifecycle(new,seq,gen)
    return _write(db,run)


def lifecycle_read(db, principal, target_id, *, authority, history=False):
    """Read state/history for an explicitly active EIC; returns no credentials."""
    _id(target_id)
    def run():
        actor_id = _principal_actor(db, principal, authority)
        _admin(db,actor_id); _role(db,target_id)
        state=_state(db,target_id)
        if not history:return state
        return tuple(db.execute('SELECT sequence,from_state,to_state,auth_generation,actor_user_id,occurred_at,reason_code FROM account_lifecycle_events WHERE user_id=? ORDER BY sequence',(target_id,)).fetchall())
    return _write(db,run)


def validate_synthetic_jwt(db, token, key, *, expected_user_id, allow_legacy=False):
    """Prototype signature/expiry/subject/generation check; never used by app."""
    _id(expected_user_id)
    if type(allow_legacy) is not bool or not isinstance(key,str) or len(key)<32: raise PolicyError('Invalid validation policy')
    try:
        claims=jwt.decode(token,key,algorithms=['HS256'],options={'require':['sub','exp']})
        if claims.get('sub') != str(expected_user_id): return False
        present='gen' in claims
        generation=claims.get('gen')
        if present and (type(generation) is not int or generation < 1): return False
        def run():
            _role(db,expected_user_id)
            state=_state(db,expected_user_id)
            if state is None:return allow_legacy and not present
            return state.state == 'active' and generation == state.generation
        return _write(db,run)
    except (jwt.InvalidTokenError, PolicyError):
        return False


def _editor(db, actor, manuscript):
    role = _role(db,actor)
    state = _state(db,actor)
    if state is None or state.state != 'active': raise PolicyError('Actor unavailable')
    if role == 'eic': return
    if role != 'editor' or not db.execute("SELECT 1 FROM initial_editor_invitations WHERE manuscript_id=? AND editor_id=? AND status='accepted'",(manuscript,actor)).fetchone():
        raise PolicyError('Actor unavailable')


def invalidate_invitation(db, principal, assignment_id, *, authority, replacement_id=None, reason='editorial_cancellation'):
    """Commit a pending cancellation/supersession; no invitation is created."""
    _id(assignment_id)
    if replacement_id is not None: _id(replacement_id)
    if (replacement_id is None and reason not in ('editorial_cancellation','reviewer_unavailable')) or (replacement_id is not None and reason != 'replacement_selected'):
        raise PolicyError('Invalid reason')
    def run():
        actor_id = _principal_actor(db, principal, authority)
        def item(aid):
            return db.execute("""SELECT a.manuscript_id,a.review_round,a.reviewer_id,a.status,i.status,i.responded_at,m.review_round,m.status
                FROM review_assignments a JOIN reviewer_invitations i ON i.assignment_id=a.assignment_id
                JOIN manuscripts m ON m.manuscript_id=a.manuscript_id WHERE a.assignment_id=?""",(aid,)).fetchone()
        source=item(assignment_id)
        if not source or source[3:6] != ('assigned','pending',None) or source[1] != source[6] or source[7] not in ('ready_for_peer_review','revised'):
            raise PolicyError('Invitation unavailable')
        _editor(db,actor_id,source[0])
        if db.execute('SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=? OR replacement_assignment_id=?',(assignment_id,assignment_id)).fetchone():
            raise PolicyError('Invitation already invalidated or part of a replacement chain')
        if replacement_id is not None:
            replacement=item(replacement_id)
            if not replacement or replacement_id == assignment_id or replacement[0:2] != source[0:2] or replacement[2] == source[2] or replacement[3:6] != ('assigned','pending',None) or replacement[1] != replacement[6] or _role(db,replacement[2]) != 'reviewer':
                raise PolicyError('Replacement unavailable')
            if db.execute('SELECT 1 FROM reviewer_invitation_invalidations WHERE assignment_id=? OR replacement_assignment_id=?',(replacement_id,replacement_id)).fetchone():
                raise PolicyError('Replacement already used or invalidated')
        iid=str(uuid4())
        db.execute('INSERT INTO reviewer_invitation_invalidations VALUES (?,?,?,?,?,?,?)',(iid,assignment_id,'cancelled' if replacement_id is None else 'superseded',replacement_id,actor_id,_stamp(),reason))
        return iid
    return _write(db,run)

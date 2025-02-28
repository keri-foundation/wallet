import asyncio
import logging
from dataclasses import dataclass
from typing import List

from hio.base import doing
from hio.help import decking
from keri import kering
from keri.app import grouping
from keri.app.habbing import Hab
from keri.core import coring, serdering
from keri.db import dbing
from ordered_set import OrderedSet as oset

from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class GroupRequester(doing.Doer):
    """Processes operations on multisig groups including inception, rotation, and interaction."""

    def __init__(self, app, hby, counselor, groups, postman):
        self.app = app
        self.hby = hby
        self.counselor = counselor
        self.groups = groups
        self.postman = postman
        self.cues = decking.Deck()

        asyncio.create_task(self.processCues())

        super().__init__()

    def enter(self):
        return super().enter()

    def multisig_incept(self, ghab, serder):
        """Creates a multisig inception message for the group."""
        icp = ghab.makeOwnInception(allowPartiallySigned=True)

        # Create a notification EXN message to send to the other agents
        exn, ims = grouping.multisigInceptExn(ghab.mhab, smids=ghab.smids, rmids=ghab.rmids, icp=icp)
        others = list(oset(ghab.smids + (ghab.rmids or [])))

        others.remove(ghab.mhab.pre)

        for recpt in others:  # this goes to other participants only as a signaling mechanism
            self.app.agent.postman.send(src=ghab.mhab.pre, dest=recpt, topic='multisig', serder=exn, attachment=ims)

        async def show():
            self.app.snack(f'Group identifier inception initialized for {ghab.pre}')

        self.app.page.run_task(show)

        prefixer = coring.Prefixer(qb64=serder.pre)
        seqner = coring.Seqner(sn=serder.sn)
        saider = coring.Saider(qb64=serder.said)
        self.counselor.start(ghab=ghab, prefixer=prefixer, seqner=seqner, saider=saider)
        self.cues.push(dict(serder=serder))

    def multisig_rotate(self, ghab, rot, smids, rmids):
        serder = serdering.SerderKERI(raw=rot)
        hby = self.hby
        rot_rmids = rmids
        _, evt_rmids = get_evt_rmids(hby, rot_rmids)

        exn, ims = grouping.multisigRotateExn(ghab=ghab, smids=smids, rmids=evt_rmids, rot=rot)

        others = list(oset(smids + (evt_rmids or [])))

        others.remove(ghab.mhab.pre)

        for recpt in others:  # Send event AND notification message to others
            self.postman.send(src=ghab.mhab.pre, dest=recpt, topic='multisig', serder=exn, attachment=bytearray(ims))

        async def show():
            self.app.snack(f'Group identifier rotation initialized for {ghab.name} | {ghab.pre}')

        self.app.page.run_task(show)

        prefixer = coring.Prefixer(qb64=ghab.pre)
        seqner = coring.Seqner(sn=ghab.kever.sn + 1)
        saider = coring.Saider(qb64=serder.said)
        self.counselor.start(ghab=ghab, prefixer=prefixer, seqner=seqner, saider=saider)
        logger.info('Started the group counselor rotate')

        self.cues.push(dict(serder=serder))
        logger.info('Pushed a cue for rotate')

    def recur(self, tyme):
        """Checks cue for group processing requests and processes any with Counselor"""
        if self.groups:
            group_op = self.groups.popleft()
            serder = group_op['serder']
            rot = group_op['rot']

            ghab = self.hby.habs[serder.pre]

            match serder.ked['t']:
                case coring.Ilks.icp | coring.Ilks.dip:
                    self.multisig_incept(ghab, serder)
                case coring.Ilks.rot | coring.Ilks.drt:
                    self.multisig_rotate(ghab, rot, group_op['smids'], group_op['rmids'])
                case _:
                    raise ValueError(f'Unsupported or invalid event type {serder.ked["t"]}')

        # return False

    async def process_multisig_incept_cue(self, cue, serder):
        prefixer = coring.Prefixer(qb64=serder.pre)
        seqner = coring.Seqner(sn=serder.sn)
        saider = coring.Saider(qb64=serder.said)

        if self.counselor.complete(prefixer=prefixer, seqner=seqner, saider=saider):
            self.app.snack(f'Multisig AID complete for {serder.pre}.')
            self.app.page.route = f'/identifiers/{serder.pre}/view'
            self.app.page.update()
            self.app.agent.notifier.rem(self.app.agent.joining[serder.pre])
            self.app.agent.noter.update()
        else:
            self.cues.push(cue)

    async def process_multisig_rotation_cue(self, cue, serder):
        prefixer = coring.Prefixer(qb64=serder.pre)
        seqner = coring.Seqner(sn=serder.sn)
        saider = coring.Saider(qb64=serder.said)

        if self.counselor.complete(prefixer=prefixer, seqner=seqner, saider=saider):
            self.app.snack(f'Multisig AID rotation complete for {serder.pre}.')
            if self.app.controls[0] and hasattr(self.app.controls[0].active_view, 'rotate_progress_ring'):
                # TODO have a better signaling mechanism to hide the progress ring
                #   This really breaks encapsulation
                await self.app.controls[0].active_view.hide_progress_ring()
            self.app.page.route = f'/identifiers/{serder.pre}/view'
            self.app.page.update()
            try:  # clear out notification if joining - only applies to joiners, not leaders
                note = self.app.agent.joining[serder.pre]
                self.app.agent.notifier.rem(note)
                self.app.agent.noter.update()
            except KeyError:
                pass

        else:
            self.cues.push(cue)

    @log_errors
    async def processCues(self):
        while True:
            if self.cues:
                cue = self.cues.popleft()
                serder = cue['serder']
                match serder.ked['t']:
                    case coring.Ilks.icp | coring.Ilks.dip:
                        await self.process_multisig_incept_cue(cue, serder)
                    case coring.Ilks.rot | coring.Ilks.drt:
                        await self.process_multisig_rotation_cue(cue, serder)
                    case _:
                        raise ValueError(f'Unsupported or invalid event type {serder.ked["t"]}')

            await asyncio.sleep(1)


def get_evt_rmids(hby, rmids):
    """
    return a tuple of member digers and rotation member IDs (AIDs) based on current event state
    set of next rotation key digests.
    """
    migers = []
    evt_rmids = []
    for rmid in rmids:
        match rmid.split(':'):
            case [mid]:  # Only prefix provided, assume latest event
                if mid not in hby.kevers:
                    raise kering.ConfigurationError(f'unknown rotation member {mid}')

                mkever = hby.kevers[mid]  # get key state for given member
                migers.append(mkever.ndigers[0])
                evt_rmids.append(mid)

            case [mid, sn]:
                if mid not in hby.kevers:
                    raise kering.ConfigurationError(f'unknown rotation member {mid}')

                dig = hby.db.getKeLast(dbing.snKey(mid, int(sn)))
                if dig is None:
                    raise kering.ConfigurationError(f'non-existant event {sn} for rotation member {mid}')

                evt = hby.db.getEvt(dbing.dgKey(mid, bytes(dig)))
                serder = serdering.SerderKERI(raw=bytes(evt))
                if not serder.estive:
                    raise kering.ConfigurationError(f'invalid event {sn} for rotation member {mid}')

                migers.append(serder.ndigers[0])
                evt_rmids.append(mid)

            case _:
                raise kering.ConfigurationError(f'invalid rmid representation {rmid}')
    return migers, evt_rmids


def create_rotation_event(hby, ghab, smids, rmids, wits, cuts, adds, isith, nsith, toad, data):
    """
    Perform multisig rotation with local Habery, GroupHab, and return a SerderKERI of the event.

    Returns a bytearray of the rotation event including all signature attachments at the end of the
    bytearray.
    """
    rot_smids = smids if smids is not None else ghab.smids
    rot_rmids = rmids if rmids is not None else rot_smids

    if wits:
        if adds or cuts:
            raise kering.ConfigurationError('you can only specify witnesses or cuts and add')
        ewits = ghab.kever.wits

        # wits= [a,b,c]  wits=[b, z]
        evt_cuts = set(ewits) - set(wits)
        evt_adds = set(wits) - set(ewits)
    else:
        evt_cuts = cuts if cuts is not None else []
        evt_adds = adds if adds is not None else []

    evt_smids = []
    merfers = []
    for smid in rot_smids:
        match smid.split(':'):
            case [mid]:  # Only prefix provided, assume latest event
                if mid not in hby.kevers:
                    raise kering.ConfigurationError(f'unknown signing member {mid}')

                mkever = hby.kevers[mid]  # get key state for given member
                merfers.append(mkever.verfers[0])
                evt_smids.append(mid)

            case [mid, sn]:
                if mid not in hby.kevers:
                    raise kering.ConfigurationError(f'unknown signing member {mid}')

                dig = hby.db.getKeLast(dbing.snKey(mid, int(sn)))
                if dig is None:
                    raise kering.ConfigurationError(f'non-existent event {sn} for signing member {mid}')

                evt = hby.db.getEvt(dbing.dgKey(mid, bytes(dig)))
                serder = serdering.SerderKERI(raw=bytes(evt))
                if not serder.estive:
                    raise kering.ConfigurationError(f'invalid event {sn} for signing member {mid}')

                merfers.append(serder.verfers[0])
                evt_smids.append(mid)

            case _:
                raise kering.ConfigurationError(f'invalid smid representation {smid}')

    migers, _ = get_evt_rmids(hby, rot_rmids)

    if ghab.mhab.pre not in evt_smids:
        raise kering.ConfigurationError(f'{ghab.mhab.pre} not in signing members {evt_smids} for this event')

    rot = ghab.rotate(
        smids=smids,
        rmids=rmids,
        isith=isith,
        nsith=nsith,
        toad=toad,
        cuts=list(evt_cuts),
        adds=list(evt_adds),
        data=data,
        verfers=merfers,
        digers=migers,
    )

    return rot


def calc_weights(num_members: int) -> str:
    """Do a simple threshold weight calculation returning an equal weight for all members."""
    match num_members:
        case 0:
            return '0'
        case 1:
            return '1'
        case _:
            return f'1/{num_members}'


@dataclass
class GroupMember:
    # Human-readable name of the member
    alias: str
    # AID Prefix of the member
    pre: str
    # Signing threshold of the member. None if not signing member.
    # int if non-fractional threshold, str if fractional threshold
    sthold: int | str | None
    # Rotation threshold of the member. None if not rotation member.
    # int if non-fractional threshold, str if fractional threshold
    rthold: int | str | None


def create_participant_fn(smids, rmids, smid_tholds, rmid_tholds):
    """
    Factory function for creating GroupMember objects closing over smids, rmids, and thresholds.
    """

    def create_participant(alias, pre):
        """Creates a GroupMember object with correct threshold"""
        sthold = smid_tholds[pre] if pre in smids else None
        rthold = rmid_tholds[pre] if pre in rmids else None
        return GroupMember(alias, pre, sthold, rthold)

    return create_participant


def filter_my_hab(habs: dict[str:Hab], multisig_participants: List[str]) -> Hab:
    """
    Get my hab from the list of multisig participants.

    This function expects there should only ever be one local hab is a participant in
    the multisig group.
    """
    for pre, hab in habs.items():
        if pre in multisig_participants:
            return hab

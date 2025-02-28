import logging
import sys
import time
from dataclasses import dataclass

from hio.base import doing
from keri.app import agenting as keriAgenting
from keri.app import connecting, habbing
from keri.app.cli.commands.local.watch import States, WatchDoer
from keri.app.habbing import GroupHab, Hab

from wallet.core.agent_events import AgentEventTypes

logger = logging.getLogger('wallet')


@dataclass
class AidKelUpdate:
    """
    Data class representing the need to update a multisig AID's key event log (KEL) to match the KEL
    as of the sequence number of the specified witness.

    Attributes:
        aid (str): The multisig identifier to update.
        sn (int): The sequence number of the multisig KEL to catch up to from the witness.
        said (str): The self-addressing identifier of the last event in the KEL to catch up to.
        wit_pre (str): The prefix of the witness to query for the missing KEL events.
        duplicitous (bool): True if duplicitous, false otherwise
    """

    aid: str
    sn: int
    said: str
    wit_pre: str
    duplicitous: bool


@dataclass
class WitnessUpdate:
    """
    Data class representing the need to update a witness that is behind by resubmitting receipts
    to it.

    Attributes:
        aid (str): The multisig identifier to update the witness for.
        sn (int): The sequence number of the multisig KEL to catch the witness up to.
        said (str): The SAID of the last event in the KEL to catch the witness up to.
        wit_pre (str): The prefix of the witness to update.
    """

    aid: str
    sn: int
    said: str
    wit_pre: str


class KELStateReader(doing.DoDoer):
    """
    Observes and synchronizes key state for AIDs based on their witnesses.
    This performs the equivalent of the `kli local watch` and `kli multisig update` commands.
    """

    def __init__(self, app, hby, watch_reqs, aid_updates, wit_updates, dup_evts, **kwa):
        """
        Creates a SyncerDoer that monitors witnesses with MailboxDirector and sends KEL updates using
        a Poster based on reading all witnesses for all AIDs for the local Agency's Habery.

        Parameters:
            app (WalletApp): Wallet application instance
            hby (habbing.Habery): Instance of Habery containing all AIDs to watch
            watch_reqs (decking.Deck): List of AIDKelUpdate requests
            aid_updates (decking.Deck): List of AidKelUpdate objects
            wit_updates (decking.Deck): List of WitnessUpdate objects
            dup_evts (decking.Deck): List of AidKelUpdate objects for duplicitous events
        """
        doers = []
        self.app = app
        self.hby = hby
        self.hbyDoer = habbing.HaberyDoer(habery=self.hby)
        self.watch_reqs = watch_reqs
        self.aid_updates = aid_updates
        self.wit_updates = wit_updates
        self.dup_evts = dup_evts

        super(KELStateReader, self).__init__(doers=doers, always=True)

    def add_if_not_exists(self, existing_items, new_items):
        """Adds an item to a collection based on the .aid prop if it doesn't already exist.
        should probably replace with a map.
        Parameters:
            existing_items (Deck): The collection to add to
            new_items (Deck): The collection to add from
        """
        for new_item in new_items:
            exists = False
            for existing_item in existing_items:
                if existing_item.aid == new_item.aid:
                    exists = True
            if not exists:
                existing_items.append(new_item)

    def syncDo(self, tymth, tock=0.0, **opts):
        """
        Reads KEL state from each witness for local Hab AIDs using the Habery from the Agent and
        posts an update to a queue for showing to the user and later processing.
        """
        # DoDoer context setup
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock

        # Read witness state for multisig AIDs only, should not have to catch up single sig AIDs
        try:
            for hab in self.hby.habs.values():
                logger.debug(f'Reading AID state for {hab.name} prefix {hab.pre}')
                if len(hab.kever.wits) == 0:
                    logger.debug(f'Hab {hab.name} has no witnesses, skipping...')
                    continue
                elif isinstance(hab, Hab):
                    logger.debug(f'Hab {hab.name} is not multisig, skipping')
                    continue

                states = yield from self.read_witness_states(hab)

                aid_upd, wit_upd, dup_evts = self.process_states(states, hab)
                self.add_if_not_exists(self.aid_updates, aid_upd)
                self.add_if_not_exists(self.wit_updates, wit_upd)
                self.add_if_not_exists(self.dup_evts, dup_evts)
        except Exception as ex:
            logger.exception(f'Error reading KEL state: {ex}')
            yield self.tock
            return

        self.app.page.run_task(self.update_identifier_page)

    async def update_identifier_page(self):
        # TODO use some better way to signal refresh of identifiers
        #   this really breaks encapsulation
        if self.app.layout.active_view == self.app.layout.identifiers:
            self.app.page.run_task(self.app.layout.identifiers.refresh_identifiers)

    def read_witness_states(self, hab):
        """Read KEL state for each witness of an AID"""
        states = []
        for wit in hab.kever.wits:
            state = yield from self.read_witness_state(hab, wit)
            if state:
                states.append(state)
        return states

    def read_witness_state(self, hab, wit):
        """
        Read the state of a KEL for a single witness

        Returns:
            WitnessState
        """
        keys = (hab.pre, wit)
        org = connecting.Organizer(hby=self.app.agent.hby)
        contact = org.get(wit)
        if contact is None:
            alias = 'None'
            logger.debug(f'KELStateReader no contact for witness {wit} in organizer')
        else:
            alias = contact['alias']

        logger.debug(f'KELState Reader: Prefix {hab.pre} checking witness {wit}')

        # Clear any existing key state from this witness in order to pull the latest
        saider = hab.db.knas.get(keys)
        temp_witstate = None
        if saider is not None:
            temp_witstate = hab.db.ksns.get((saider.qb64,))
            self._clear_witness_keystate(hab, wit, saider)

        witer = keriAgenting.messenger(hab, wit)
        self.extend([witer])

        msg = hab.query(pre=hab.pre, src=wit, route='ksn')
        witer.msgs.append(bytearray(msg))

        start = time.perf_counter()
        while not witer.idle:
            end = time.perf_counter()
            if end - start > 10:
                break

            yield self.tock

        self.remove([witer])

        start = time.perf_counter()
        skip = False
        while True:
            if (saider := hab.db.knas.get(keys)) is not None:
                logger.debug(f'Response received from {alias} | {wit}')
                break

            end = time.perf_counter()
            if end - start > 10:
                logger.error(f'No response received from {alias} | {wit}')
                sys.stdout.flush()
                skip = True
                # If we don't get a response from the witness then replace the old keystate
                self._restore_witness_state(hab, wit, saider, temp_witstate)
                break

            yield self.tock

        if skip:
            return None

        mystate = hab.kever.state()
        witstate = hab.db.ksns.get((saider.qb64,))

        return WatchDoer.diffState(wit, mystate, witstate)

    def _clear_witness_keystate(self, hab, wit, saider):
        """
        Clear the witness key state from the database
        """
        keys = (hab.pre, wit)
        hab.db.knas.rem(keys)
        hab.db.ksns.rem((saider.qb64,))

    def _restore_witness_state(self, hab, wit, saider, temp_witstate):
        """
        Restore the witness state to the previous state if the witness does not respond
        """
        if temp_witstate is None:
            raise ValueError('No previous witness state to restore')
        hab.db.knas.pin(keys=(hab.pre, wit), val=saider)
        hab.db.ksns.pin(keys=(saider.qb64,), val=temp_witstate)

    @staticmethod
    def create_aid_duplicity(pre, state):
        return AidKelUpdate(aid=pre, sn=state.sn, said=state.dig, wit_pre=state.wit, duplicitous=True)

    @staticmethod
    def create_aid_update(pre, state):
        return AidKelUpdate(aid=pre, sn=state.sn, said=state.dig, wit_pre=state.wit, duplicitous=False)

    @staticmethod
    def create_wit_update(pre, state):
        return WitnessUpdate(
            aid=pre,
            sn=state.sn,
            said=state.dig,
            wit_pre=state.wit,
        )

    def process_states(self, states, hab):
        """
        Creates KEL updates, witness updates, and duplicity results from witness states
        Returns:
            tuple of aid_updates, wit_updates, duplicitous events
        """
        aid_updates = []
        wit_updates = []
        duplicitous = []

        # First check for any duplicity, if so get out of here
        dups = [state for state in states if state.state == States.duplicitous]
        ahds = [state for state in states if state.state == States.ahead]
        bhds = [state for state in states if state.state == States.behind]
        if len(dups) > 0:
            logger.critical('The following witnesses have a duplicitous event:')
            for state in dups:
                logger.critical(f'\tWitness {state.wit} at Seq No. {state.sn} with digest: {state.dig}')
                aid_updates.append(self.create_aid_duplicity(hab.pre, state))
            logger.critical('Further action must be taken to recover from the duplicity')

        elif len(ahds) > 0:
            # Only group habs can be behind their witnesses
            if not isinstance(hab, GroupHab):
                logger.error('ERROR: Single sig AID behind witnesses, aborting for this AID')
                return [], [], []

            # First check for duplicity among the witnesses that are ahead (possible only if toad is below
            # super majority)
            digs = set([state.dig for state in ahds])
            if len(digs) > 1:  # Duplicity across witness sets
                logger.critical(f'There are multiple duplicitous events on witnesses for {hab.pre}')
                logger.critical('We recommend you abandon this AID')
                for state in ahds:
                    duplicitous.append(self.create_aid_duplicity(hab.pre, state))

            else:  # all witnesses that are ahead agree on the event
                logger.debug('The following witnesses have an event that is ahead of the local KEL:')
                for state in ahds:
                    logger.debug(f'\tWitness {state.wit} at Seq No. {state.sn} SAID: {state.dig}')

            logger.debug(f'{len(ahds)} local key state updates needed')
            for state in ahds:
                aid_updates.append(self.create_aid_update(hab.pre, state))

            if len(bhds) > 0:
                logger.debug(f'{len(bhds)} witnesses are behind and need to be caught up.')
                for state in bhds:
                    logger.debug(f'\tWitness {state.wit} at Seq No. {state.sn} SAID: {state.dig}')
                    wit_updates.append(self.create_wit_update(hab.pre, state))

        elif len(bhds) > 0:
            logger.debug(f'{len(bhds)} witnesses are behind and need to be caught up.')
            for state in bhds:
                print(f'\tWitness {state.wit} at Seq No. {state.sn} SAID: {state.dig}')
                wit_updates.append(self.create_wit_update(hab.pre, state))
        else:
            logger.debug(
                f'Local key state is consistent with the {len(states)} (out of '
                f'{len(hab.kever.wits)} total) witnesses that responded.'
            )

        return aid_updates, wit_updates, duplicitous

    def recur(self, tyme, deeds=None):
        if self.watch_reqs:  # A single request is enough to re-request checking all AIDs.
            logger.debug('Checking for KEL state updates')
            self.extend([doing.doify(self.syncDo)])
            self.watch_reqs.clear()

        return super(KELStateReader, self).recur(tyme, deeds)


class KELStateUpdater(doing.DoDoer):
    """
    Updates the KEL with the most recent events for a local AID, usually multisig, that does not
    have the latest KEL events as compared with a set of witnesses.
    """

    def __init__(self, app, hby, update_reqs):
        doers = []
        self.app = app
        self.hby = hby
        self.update_reqs = update_reqs

        self.witq = keriAgenting.WitnessInquisitor(hby=self.hby)
        doers.extend([self.witq])
        super(KELStateUpdater, self).__init__(doers=doers, always=True, tock=1.0)

    async def update_identifier_page(self):
        if self.app.layout.active_view == self.app.layout.identifiers:
            identifiers = self.app.layout.identifiers
            await identifiers.refresh_identifiers()
            await self.app.page.dialog.close_confirm()
            self.app.page.dialog = None
            self.app.page.update()

    def updateDo(self, tymth, tock=0.0, **opts):
        # enter context
        self.wind(tymth)
        self.tock = tock
        _ = yield self.tock

        req = self.update_reqs.popleft()
        logger.info(f'Processing KEL State update {req.aid}')

        hab = self.hby.habByPre(req.aid)
        pre = hab.pre
        sn = req.sn
        said = req.said
        wit = req.wit_pre

        keys = (pre, wit)

        # Check for Key State from this Witness and remove if exists
        logger.debug(f'Querying witness {wit}')
        saider = hab.db.knas.get(keys)
        if saider is not None:
            hab.db.knas.rem(keys)
            hab.db.ksns.rem((saider.qb64,))

        witer = keriAgenting.messenger(hab, wit)
        self.extend([witer])

        msg = hab.query(pre=pre, src=wit, route='ksn')
        witer.msgs.append(bytearray(msg))

        while not witer.idle:
            yield self.tock

        self.remove([witer])

        start = time.perf_counter()
        while True:
            if (saider := hab.db.knas.get(keys)) is not None:
                break

            end = time.perf_counter()
            if end - start > 10:
                logger.error('No response received from witness, exiting.')
                return

            yield self.tock

        logger.debug('KEL state update received from witness')

        witstate = hab.db.ksns.get((saider.qb64,))
        if int(witstate.s, 16) != sn and witstate.d != said:
            logger.error(f'Witness state ({witstate.s}, {witstate.d}) does not match requested state.')
            return

        logger.debug('Witness at requested state, updating now...')
        self.witq.query(src=pre, pre=pre, r='logs')

        while True:
            yield self.tock
            kever = hab.kever
            if kever.serder.said == said:
                break

        # When the count of updates goes to zero then refresh the identifier page
        logger.info('Finished updating KEL state')
        for upd in self.app.agent.aid_updates:
            if upd.aid == req.aid:
                self.app.agent.aid_updates.remove(upd)
                # self.app.page.run_task(self.update_identifier_page()) # TODO consider running this from here instead of for event
                self.app.agent_events.push(dict(event_type=AgentEventTypes.KEL_UPDATE_COMPLETE.value, aid=req.aid))
                break
        return

    def recur(self, tyme, deeds=None):
        if self.update_reqs:
            self.extend([doing.doify(self.updateDo)])

        return super(KELStateUpdater, self).recur(tyme, deeds)

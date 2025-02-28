"""
Agenting module for the Wallet application
"""

import asyncio
import logging

import flet as ft
from hio.base import doing, tyming
from hio.help import decking
from keri.app import (
    agenting,
    challenging,
    connecting,
    delegating,
    forwarding,
    grouping,
    habbing,
    indirecting,
    notifying,
    oobiing,
    querying,
    signaling,
    storing,
)
from keri.core import coring, eventing, routing
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import credentialing, verifying
from keri.vdr.eventing import Tevery

from wallet.core.grouping import GroupRequester
from wallet.core.syncing import KELStateReader, KELStateUpdater
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class Agent(doing.DoDoer):
    """
    The top level object and DoDoer representing a Habery for a
    remote controller and all associated processing
    """

    def __init__(self, app, hby, rgy):
        self.hby = hby
        self.rgy = rgy

        self.swain = delegating.Anchorer(hby=hby)
        self.counselor = grouping.Counselor(hby=hby, swain=self.swain)
        self.org = connecting.Organizer(hby=hby)

        oobiery = oobiing.Oobiery(hby=hby)

        self.cues = decking.Deck()
        self.groups = decking.Deck()
        self.anchors = decking.Deck()
        self.witners = decking.Deck()
        self.queries = decking.Deck()
        self.exchanges = decking.Deck()
        self.joining = {}

        self.aid_updates = decking.Deck()  # For catching multisig group AIDs up to latest KEL state
        self.wit_updates = decking.Deck()  # For catching witnesses up to latest KEL state
        self.dup_evts = decking.Deck()  # For showing detected duplicity
        self.watch_reqs = decking.Deck()  # for requesting the KEL reader to watch all prefixes
        self.update_reqs = decking.Deck()  # for requesting the KEL updater perform an update

        receiptor = agenting.Receiptor(hby=hby)
        self.postman = forwarding.Poster(hby=hby)
        self.witPub = agenting.WitnessPublisher(hby=self.hby)
        self.witDoer = agenting.WitnessReceiptor(hby=self.hby)
        self.submitDoer = agenting.WitnessReceiptor(hby=self.hby, force=True, tock=5.0)

        self.rep = storing.Respondant(hby=hby, cues=self.cues, mbx=storing.Mailboxer(name=self.hby.name, temp=self.hby.temp))

        doers = [
            habbing.HaberyDoer(habery=hby),
            receiptor,
            self.postman,
            self.witPub,
            self.rep,
            self.swain,
            self.counselor,
            self.witDoer,
            self.submitDoer,
            *oobiery.doers,
        ]

        signaler = signaling.Signaler()
        self.notifier = notifying.Notifier(hby=hby, signaler=signaler)
        self.mux = grouping.Multiplexor(hby=hby, notifier=self.notifier)

        # Initialize all the credential processors
        self.verifier = verifying.Verifier(hby=hby, reger=rgy.reger)
        self.registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=self.counselor)
        self.credentialer = credentialing.Credentialer(
            hby=self.hby, rgy=self.rgy, registrar=self.registrar, verifier=self.verifier
        )

        challengeHandler = challenging.ChallengeHandler(db=hby.db, signaler=signaler)

        handlers = [challengeHandler]
        self.exc = exchanging.Exchanger(hby=hby, handlers=handlers)

        grouping.loadHandlers(exc=self.exc, mux=self.mux)
        protocoling.loadHandlers(hby=self.hby, exc=self.exc, notifier=self.notifier)

        self.rvy = routing.Revery(db=hby.db, cues=self.cues)
        self.kvy = eventing.Kevery(db=hby.db, lax=True, local=False, rvy=self.rvy, cues=self.cues)
        self.kvy.registerReplyRoutes(router=self.rvy.rtr)

        self.tvy = Tevery(reger=self.verifier.reger, db=hby.db, local=False, cues=self.cues)

        self.tvy.registerReplyRoutes(router=self.rvy.rtr)
        self.mbx = indirecting.MailboxDirector(
            hby=self.hby,
            topics=['/receipt', '/multisig', '/replay', '/delegate', '/credential', '/challenge', '/reply'],
            exc=self.exc,
            kvy=self.kvy,
            tvy=self.tvy,
            rvy=self.rvy,
            verifier=self.verifier,
        )

        self.cloner = ExchangeCloner(hby=hby)
        self.noter = Noter(app=app, hby=hby, notifier=self.notifier, tock=3.0)
        self.kelStateReader = KELStateReader(
            app=app,
            hby=hby,
            watch_reqs=self.watch_reqs,
            aid_updates=self.aid_updates,
            wit_updates=self.wit_updates,
            dup_evts=self.dup_evts,
        )

        self.kelStateUpdater = KELStateUpdater(app=app, hby=hby, update_reqs=self.update_reqs)
        doers.extend(
            [
                self.mbx,
                Querier(hby=hby, kvy=self.kvy, queries=self.queries),
                Witnesser(app=app, receiptor=receiptor, witners=self.witners),
                Delegator(hby=self.hby, swain=self.swain, anchors=self.anchors),
                ExchangeSender(hby=hby, exc=self.exc, postman=self.postman, exchanges=self.exchanges),
                GroupRequester(app=app, hby=hby, counselor=self.counselor, groups=self.groups, postman=self.postman),
                self.cloner,
                self.noter,
                self.kelStateReader,
                self.kelStateUpdater,
                KELWatchScheduler(self.watch_reqs),
            ]
        )
        self.watch_reqs.append(dict())

        super(Agent, self).__init__(doers=doers, always=True)

    def witness_resubmit(self, pre):
        self.submitDoer.msgs.append(dict(pre=pre))


class KELWatchScheduler(doing.Doer):
    """Schedules a KEL watch request every self.tock seconds, defaults to 5"""

    def __init__(self, watch_reqs, tock=7.5, **kwa):
        self.watch_reqs = watch_reqs
        self.tock = tock
        super(KELWatchScheduler, self).__init__(tock=self.tock, **kwa)

    def recur(self, tyme):
        self.watch_reqs.append(dict())  # Schedule a watch request


class Witnesser(doing.Doer):
    def __init__(self, app, receiptor, witners):
        self.app = app
        self.receiptor = receiptor
        self.witners = witners
        self.cues = decking.Deck()
        asyncio.create_task(self.processCues())

        super(Witnesser, self).__init__()

    def recur(self, tyme=None):
        while True:
            if self.witners:
                msg = self.witners.popleft()
                serder = msg['serder']

                # If we are a rotation event, may need to catch new witnesses up to current key state
                if serder.ked['t'] in (coring.Ilks.rot, coring.Ilks.drt):
                    adds = serder.ked['ba']
                    for wit in adds:
                        yield from self.receiptor.catchup(serder.pre, wit)

                yield from self.receiptor.receipt(serder.pre, serder.sn)
                self.cues.push(msg)

            yield self.tock

    async def processCues(self):
        while True:
            if self.cues:
                cue = self.cues.popleft()
                serder = cue['serder']
                self.app.snack(f'Witness receipts received for {serder.pre}.')

            await asyncio.sleep(1.0)


class Delegator(doing.Doer):
    def __init__(self, hby, swain, anchors):
        self.hby = hby
        self.swain = swain
        self.anchors = anchors
        super(Delegator, self).__init__()

    def recur(self, tyme=None):
        if self.anchors:
            msg = self.anchors.popleft()
            sn = msg['sn'] if 'sn' in msg else None

            proxy = msg['proxy']
            phab = self.hby.habByName(proxy)

            self.swain.delegation(pre=msg['pre'], sn=sn, proxy=phab)

        return False


class ExchangeSender(doing.Doer):
    def __init__(self, hby, postman, exc, exchanges):
        self.hby = hby
        self.postman = postman
        self.exc = exc
        self.exchanges = exchanges
        super(ExchangeSender, self).__init__()

    def recur(self, tyme):
        if self.exchanges:
            msg = self.exchanges.popleft()
            said = msg['said']
            if not self.exc.complete(said=said):
                self.exchanges.append(msg)
                return False

            serder, pathed = exchanging.cloneMessage(self.hby, said)

            src = msg['src']
            pre = msg['pre']
            rec = msg['rec']
            topic = msg['topic']
            hab = self.hby.habs[pre]
            if self.exc.lead(hab, said=said):
                atc = exchanging.serializeMessage(self.hby, said)
                del atc[: serder.size]
                for recp in rec:
                    self.postman.send(src=src, dest=recp, topic=topic, serder=serder, attachment=atc)


class ExchangeCloner(doing.Doer):
    def __init__(self, hby):
        self.hby = hby
        self.notes = decking.Deck()
        self.cloned = dict()

        super().__init__()

    def recur(self, tyme):
        if self.notes:
            said = self.notes.popleft()

            serder, _ = exchanging.cloneMessage(self.hby, said)

            self.cloned[said] = serder

        return False

    def clone(self, said):
        self.notes.append(said)


class Noter(doing.Doer):
    def __init__(self, app, hby, notifier, **kwa):
        self.app = app
        self.hby = hby
        self.notifier = notifier
        self.start = 0
        self.count = 0
        self.notes = []

        super(Noter, self).__init__(**kwa)

    async def show_new_notifications(self):
        self.app.snack('New notifications')

    async def show_unread(self):
        self.app.notificationsButton.icon = ft.Icons.NOTIFICATIONS_ACTIVE_ROUNDED
        self.app.page.update()

    async def show_read(self):
        self.app.notificationsButton.icon = ft.Icons.NOTIFICATIONS_ROUNDED
        self.app.page.update()

    async def show_no_notifications(self):
        self.app.notificationsButton.icon = ft.Icons.NOTIFICATIONS_NONE_ROUNDED
        self.app.page.update()

    def enter(self):
        self.count = self.notifier.getNoteCnt()
        return super().enter()

    def recur(self, tyme):
        del tyme
        self.update()

        return False

    def update(self):
        if self.notifier.getNoteCnt() > self.count:
            self.app.page.run_task(self.show_new_notifications)
            self.count = self.notifier.getNoteCnt()

        self.notes = self.notifier.getNotes(start=self.start, end=self.count)
        self.app.notes = self.notes

        unread_notes = [note for note in self.notes if note.read is False]
        read_notes = [note for note in self.notes if note.read is True]

        if unread_notes:
            self.app.page.run_task(self.show_unread)
        elif read_notes:
            self.app.page.run_task(self.show_read)
        else:
            self.app.page.run_task(self.show_no_notifications)


def make_query(local_hab_alias: str, destination_prefix: str) -> dict:
    """
    Create a key state query message to direct the querier to retrieve key state
    """
    return {'src': local_hab_alias, 'pre': destination_prefix}


class Querier(doing.DoDoer):
    def __init__(self, hby, queries, kvy):
        self.hby = hby
        self.queries = queries
        self.kvy = kvy

        super(Querier, self).__init__(always=True)

    def recur(self, tyme, deeds=None):
        """Processes query requests submitting any on the cue"""
        if self.queries:
            msg = self.queries.popleft()
            src = msg['src']
            pre = msg['pre']
            logger.info(f'Querying from {src} for key state from {pre}...')

            hab = self.hby.habByName(src)

            if 'sn' in msg:
                seqNoDo = querying.SeqNoQuerier(hby=self.hby, hab=hab, pre=pre, sn=msg['sn'])
                self.extend([seqNoDo])
            elif 'anchor' in msg:
                pass
            else:
                qryDo = querying.QueryDoer(hby=self.hby, hab=hab, pre=pre, kvy=self.kvy)
                self.extend([qryDo])

        return super(Querier, self).recur(tyme, deeds)


def runController(app, hby, rgy, expire=0.0):
    """
    Runs an Agent with a Doist as a HioTask
    Returns an Agent, the task for the running Agent, and the shutdown event for the task
    """
    agent = Agent(app=app, hby=hby, rgy=rgy)
    doers = [agent]

    event = asyncio.Event()

    tock = 0.03125
    doist = doing.Doist(doers=doers, limit=expire, tock=tock, real=True)
    htask = HioTask(doist=doist, event=event)

    try:
        agent_task = asyncio.create_task(htask.run())
    except Exception as ex:
        logger.exception('Error creating agent task')
        raise ex

    return agent, agent_task, event


async def run_hio_task(doers, expire=0.0):
    logger.info(f'Running HioTask with {len(doers)} doers')
    doist = doing.Doist(doers=doers, limit=expire, tock=0.03125, real=True)
    htask = HioTask(doist=doist)

    await htask.run()
    logger.info('HioTask complete')


class HioTask:
    def __init__(self, doist, event):
        """
        A task that allows scheduling a HIO Doist to run KERIpy Doers as an AsyncIO task.

        Parameters:
            doist (doing.Doist): the Doist to run
            event (asyncio.Event): shutdown event signal triggering Doist.exit()
        """
        self.doist = doist
        self.event = event

    @log_errors
    async def run(self, limit=None, tyme=None):
        self.doist.done = False

        if limit is not None:  # time limt for running if any. useful in test
            self.doist.limit = abs(float(limit))

        if tyme is not None:  # re-initialize starting tyme
            self.doist.tyme = tyme

        try:  # always clean up resources upon exception
            self.doist.enter()  # runs enter context on each doer

            tymer = tyming.Tymer(tymth=self.doist.tymen(), duration=self.doist.limit)
            self.doist.timer.start()

            while True:  # until doers complete or exception or keyboardInterrupt
                if self.event.is_set():  # event set means HioTask is either shutting down or done
                    break
                try:
                    self.doist.recur()  # increments .tyme runs recur context

                    if self.doist.real:  # wait for real time to expire
                        while not self.doist.timer.expired:
                            await asyncio.sleep(max(0.0, self.doist.timer.remaining))
                        self.doist.timer.restart()  # no time lost

                    if not self.doist.deeds:  # no deeds
                        self.doist.done = True
                        break  # break out of forever loop

                    if self.doist.limit and tymer.expired:  # reached time limit
                        break  # break out of forever loop

                except KeyboardInterrupt:  # use CNTL-C to shutdown from shell
                    break

                except SystemExit:  # Forced shutdown of process
                    raise

                except Exception as e:  # Unknown exception
                    logger.exception(e)
                    logger.error(f'HioTask exception: {e}')
                    raise

        except Exception as ex:
            logger.error(f'Error occurred in HioTask: {str(ex)}')
            raise ex
        finally:  # finally clause always runs regardless of exception or not.
            self.doist.exit()  # force close remaining deeds throws GeneratorExit
            logger.info('HioTask closed')


async def close_agent_task(agent_task, event, timeout=5.0):
    """Send shutdown signal to event to close agent task with an optional timeout"""
    if asyncio.isfuture(agent_task):
        event.set()
        try:
            await asyncio.wait_for(agent_task, timeout)
        except asyncio.TimeoutError:
            logger.warning(f'Agent task shutdown timed out after {timeout} seconds.')
            agent_task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            logger.error(f'Exception on agent close: {ex}', exc_info=True)
        return True
    return False

import asyncio
import json
import logging
import pprint
from pathlib import Path

import flet as ft
from flet.core.icons import Icons
from flet.core.page import Page
from hio.help import decking
from keri.app import connecting
from keri.app.keeping import Algos
from keri.core import coring
from keri.core.coring import Tiers

from wallet.app import drawing
from wallet.app.assets import Assets
from wallet.app.layout import Layout
from wallet.core.agenting import close_agent_task
from wallet.core.configing import WalletConfig
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class WalletApp(ft.Stack):
    """
    Main wallet application control. Expressed as a Stack control

    Attributes:
        agent: Agent
        page: ft.Page
    """

    def __init__(self, page: ft.Page, config: WalletConfig):
        super().__init__()
        # Flet config props
        self.environment = config.environment
        self.config = config
        self.layout = None  # filled in by did_mount
        self.name = config.app_name

        # ft.Stack attributes
        self.width = 1024
        self.height = 768
        self.current_top = 0
        self.current_left = 0

        # KERI props
        self.agent = None  # Will be set by the AgentDrawer
        self.agent_task = None  # Will be set by the AgentDrawer
        self.agent_shutdown_event = asyncio.Event()  # Will be set by the AgentDrawer
        self.wit_pools = self.load_witness_pools(config)

        # AgentEvents
        self.agent_events = decking.Deck()

        self.base = ''
        self.temp = False
        self.tier = Tiers.low
        self.algo = Algos.salty
        self.salt = coring.randomNonce()[2:23]

        # Flet Page state
        self.notes = []
        self.witnesses = []
        self.members = []

        # Flet components that will be filled in did_mount
        self.agentDrawer = None
        self.agentDrawerButton = None
        self.notificationsButton = None
        self.lockButton = None
        self.actions = []

    @staticmethod
    def load_witness_pools(config: WalletConfig):
        with open(config.witness_pool_path, 'r') as f:
            wit_pools = json.load(f)
            pp = pprint.PrettyPrinter(indent=2, sort_dicts=False)
            logger.info('Loaded witness pools from %s\n%s', config.witness_pool_path, pp.pformat(wit_pools))
        return wit_pools

    async def close(self):
        logger.info('Wallet App closing')
        closed = await close_agent_task(self.agent_task, self.agent_shutdown_event)
        self.agent_task = None  # Clear existing HioTask reference
        if closed:
            logger.info(f'Disconnected agent {self.agent.hby.name}')
        self.page.window.destroy()
        logger.info('Wallet App closed')

    async def on_window_event(self, e):
        if e.data == 'close':
            await self.close()

    def did_mount(self):
        """
        Lifecycle function called after component is mounted to page.
        Flet populates the self.page attribute prior to calling the did_mount lifecycle function.
        """
        logger.info('Wallet App mounted')
        page: Page = self.page  # This is the page that Flet created for us
        page.title = self.name if self.environment.value == 'production' else f'{self.name} [{self.environment.value}]'
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        page.on_route_change = self.route_change
        page.window.width = 1024
        page.window.height = 768
        page.window.prevent_close = True
        page.window.on_event = self.on_window_event

        self.agentDrawer = drawing.AgentDrawer(app=self, page=page, open=True, config=self.config)
        self.agentDrawerButton = ft.IconButton(
            Icons.WALLET_ROUNDED,
            tooltip='Wallets',
            on_click=self.toggle_drawer,
        )
        self.notificationsButton = ft.IconButton(
            Icons.NOTIFICATIONS_NONE_ROUNDED,
            on_click=self.show_notifications,
        )
        self.lockButton = ft.IconButton(ft.Icons.LOCK, on_click=self.lock)

        self.actions = [self.agentDrawerButton]
        page.appbar = ft.AppBar(
            leading=ft.Container(
                Assets().logo_icon,
                border_radius=ft.border_radius.all(5),
                padding=ft.padding.all(2),
                margin=ft.margin.all(10),
            ),
            title=ft.Text(self.name, weight=ft.FontWeight.BOLD),
            center_title=False,
            actions=self.actions,
        )

        self.layout = Layout(
            self,
            page,
            expand=True,
            vertical_alignment='start',
        )
        self.controls = [self.layout]

    @log_errors
    async def toggle_drawer(self, _):
        await asyncio.sleep(0.1)
        self.page.end_drawer = self.agentDrawer
        drawer = self.page.end_drawer
        if drawer.open:
            self.page.close(drawer)
        else:
            self.page.open(drawer)
        self.page.end_drawer.update()

    @log_errors
    async def route_change(self, _):
        tr = ft.TemplateRoute(self.page.route)
        if tr.match('/'):
            self.page.go('/splash')
        elif tr.match('/identifiers'):
            self.layout.set_identifiers_list()
        elif tr.match('/identifiers/create'):
            self.layout.set_identifier_create()
        elif tr.match('/identifiers/:prefix/view'):
            self.layout.set_identifier_view(tr.prefix)
        elif tr.match('/identifiers/:prefix/rotate'):
            self.layout.set_identifier_rotate(tr.prefix)
        elif tr.match('/contacts'):
            self.layout.set_contacts_list()
        elif tr.match('/contacts/create'):
            self.layout.set_contact_create()
        elif tr.match('/contacts/:prefix/view'):
            self.layout.set_contact_view(tr.prefix)
        elif tr.match('/settings'):
            self.layout.set_settings_view()
        elif tr.match('/notifications'):
            self.layout.set_notifications_view()
        elif tr.match('/notifications/:note_id'):
            self.layout.set_notifications_note_view(tr.note_id)
        elif tr.match('/witnesses'):
            self.layout.set_witnesses_view()
        elif tr.match('/witnesses/create'):
            self.layout.set_witness_add_view()
        elif tr.match('/witnesses/:prefix/view'):
            self.layout.set_witness_view(tr.prefix)
        elif tr.match('/identifiers/:prefix/view'):
            self.layout.set_witness_view()
        elif tr.match('/splash'):
            logger.info('Route change to /splash')
            self.layout.set_splash_view()

        self.page.update()

    async def show_notifications(self, e=None):
        self.page.route = '/notifications'
        self.page.update()

    async def lock(self, e=None):
        closed = await close_agent_task(self.agent_task, self.agent_shutdown_event)
        self.agent_task = None  # Clear existing HioTask reference
        if closed:
            logger.info(f'Disconnected agent {self.agent.hby.name}')
            self.agent = None
            self.page.floating_action_button = None
            self.layout.navbar.visible = False
            self.notificationsButton.visible = False
            self.lockButton.visible = False
            self.page.hby_name = None
            self.page.snack_bar = None
            self.page.route = '/splash'
            self.page.update()

    async def refreshContacts(self):
        org = connecting.Organizer(hby=self.agent.hby)
        contacts = []
        for c in org.list():
            aid = c['id']
            accepted = [saider.qb64 for saider in self.agent.hby.db.chas.get(keys=(aid,))]
            received = [saider.qb64 for saider in self.agent.hby.db.reps.get(keys=(aid,))]
            valid = set(accepted) & set(received)

            challenges = []
            for said in valid:
                exn = self.agent.hby.db.exns.get(keys=(said,))
                challenges.append(dict(dt=exn.ked['dt'], words=exn.ked['a']['words']))

            c['challenges'] = challenges

            wellKnowns = []
            wkans = self.agent.hby.db.wkas.get(keys=(aid,))
            for wkan in wkans:
                wellKnowns.append(dict(url=wkan.url, dt=wkan.dt))

            c['wellKnowns'] = wellKnowns

            contacts.append(c)

        await self.layout.contacts.set_contacts(contacts)
        self.layout.contacts.update()

    def reload(self):
        if self.agent is not None:
            self.layout.navbar.visible = True
            self.notificationsButton.visible = True
            self.lockButton.visible = True

    def reload_witnesses_and_members(self):
        org = connecting.Organizer(hby=self.agent.hby)

        self.witnesses.clear()
        self.members.clear()
        for contact in org.list():
            prefixer = coring.Prefixer(qb64=contact['id'])
            if not prefixer.transferable:
                self.witnesses.append(contact)
            else:
                self.members.append(contact)

        if all('alias' in c for c in self.witnesses):
            self.witnesses = sorted(self.witnesses, key=lambda c: c['alias'])
        else:
            self.witnesses = sorted(self.witnesses, key=lambda c: c['id'])
        if all('alias' in c for c in self.members):
            self.members = sorted(self.members, key=lambda c: c['alias'])
        else:
            self.members = sorted(self.members, key=lambda c: c['id'])

    # on_change
    @property
    def agent(self):
        return self._agent

    @agent.setter
    def agent(self, agent):
        self._agent = agent
        if self._agent is not None:
            self.layout.navbar.visible = True
            self.layout.splash.visible = False
            self.actions.insert(0, self.notificationsButton)
            self.actions.insert(len(self.actions), self.lockButton)
            self.page.update()

    def snack(self, message, duration=5000):
        """Open the snack bar with the given message for the given duration."""
        self.page.snack_bar = ft.SnackBar(ft.Text(message), duration=duration)
        self.page.open(self.page.snack_bar)

    @property
    def hby(self):
        return self.agent.hby if self.agent is not None else None

    @staticmethod
    def environments():
        dbhome = Path('/usr/local/var/keri/db')
        if not dbhome.exists():
            dbhome = Path(f'{Path.home()}/.keri/db')

        if not dbhome.is_dir():
            return []

        envs = []
        for p in dbhome.iterdir():
            envs.append(p.stem)

        return envs

import logging

import flet as ft

from wallet.app import agenting
from wallet.app.agenting import AgentInitialization
from wallet.core.agenting import close_agent_task
from wallet.core.configing import Environments, WalletConfig
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class AgentDrawer(ft.NavigationDrawer):
    def __init__(self, app, page: ft.Page, open=False, config: WalletConfig = None):
        super(AgentDrawer, self).__init__()
        self.config = config
        self.page = page
        self.app = app
        self.open = open
        self.agent_init: AgentInitialization = AgentInitialization(self.app, self.page, self.config)

        self.update_agents()

        self.on_change = self.agent_change
        self.on_dismiss = self.drawer_dismiss

    def update_agents(self):
        agents = []

        for agent in sorted(self.app.environments()):
            if self.config.environment == Environments.DEVELOPMENT:
                if agent in ['wan', 'wil', 'wes', 'wit', 'wub', 'wyz']:  # skip development witnesses
                    logger.debug(f'Skipping development witness {agent}')
                    continue
            logger.info(f'found agent {agent}')
            agents.append(
                ft.NavigationDrawerDestination(
                    icon_content=ft.Icon(ft.Icons.IRON),
                    label=agent,
                )
            )
        self.controls = [
            ft.Container(height=12),
            ft.Container(
                content=ft.Row(controls=[ft.Container(width=16), ft.Icon(ft.Icons.WALLET_ROUNDED), ft.Text('Wallets')]),
                height=64,
            ),
            ft.Divider(thickness=2),
            ft.NavigationDrawerDestination(
                icon_content=ft.Icon(ft.Icons.ADD_ROUNDED),
                label='Initialize new wallet',
            ),
            *agents,
        ]

    async def drawer_dismiss(self, _):
        self.update()

    async def close_existing_agent(self):
        closed = await close_agent_task(self.app.agent_task, self.app.agent_shutdown_event)
        self.app.agent_task = None  # Clear existing HioTask reference
        if closed:
            self.page.title = self.app.name
            self.page.route = '/'
            self.page.update()
            self.app.snack(f'Closed connection to {self.app.hby.name}')
            logger.info(f'Closed agent {self.app.hby.name}')

    @log_errors
    async def agent_change(self, e):
        self.page.close(self.page.end_drawer)
        selected = e.control.controls[e.control.selected_index + 3]

        if selected.label == 'Initialize new wallet':
            self.page.dialog = self.agent_init
            self.page.open(self.page.dialog)
        elif hasattr(self.page, 'hby_name') and self.page.hby_name == selected.label:
            # already connected to this agent
            self.app.snack(f'Already connected to {selected.label}')
        else:  # Is different agent. Close existing and connect to new
            await self.close_existing_agent()
            self.page.dialog = agenting.AgentConnection(self.app, self.page, self.config, selected.label)
            self.page.open(self.page.dialog)

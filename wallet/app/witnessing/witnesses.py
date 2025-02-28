"""
Identifiers module for the Wallet application.
"""

import datetime
import logging

import flet as ft
from keri.app import connecting

from wallet.app.witnessing.witness import WitnessBase
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class Witnesses(WitnessBase):
    """
    Class representing witnesses in the application.

    Attributes:
        page (ft.Page): The page object associated with the app.
        list (ft.Column): The column object representing the list of witnesses.
    """

    def __init__(self, app):
        self.app = app
        self.page: ft.Page = app.page
        self.list = ft.Column([], spacing=0, expand=True)

        super().__init__(app, ft.Container(content=self.list, padding=ft.padding.only(bottom=125)))

    def did_mount(self):
        self.page.run_task(self.refresh_witnesses)

    async def refresh_witnesses(self):
        """
        Refreshes the witnesses
        """
        org = connecting.Organizer(hby=self.app.agent.hby)
        await self.set_witnesses(org.list())
        self.page.update()

    async def add_witness(self, _):
        """
        Adds an identifier to the application.

        This method sets the route to "/witnesses/create" and updates the page asynchronously.

        Parameters:
        - _: Placeholder parameter (unused)

        Returns:
        - None
        """
        self.app.page.route = '/witnesses/create'
        self.app.page.update()

    @log_errors
    async def set_witnesses(self, contacts):
        """
        Sets the witnesses for the given list of contacts.

        Args:
            contacts (list): A list of habs to set identifiers for.

        Returns:
            None
        """
        contacts = sorted(contacts, key=lambda c: c['alias'])
        contacts = list(filter(lambda c: 'type' in c and 'witness' in c['type'], contacts))

        self.list.controls.clear()

        if len(contacts) == 0:
            self.list.controls.append(
                ft.Container(
                    content=ft.Text(
                        'No witnesses found.',
                    ),
                    padding=ft.padding.all(20),
                )
            )
        else:
            for contact in contacts:
                print('contact', contact)
                pre = contact['id']
                print('pre', pre)
                kever = self.app.agent.hby.kevers[pre]

                dt = None
                if 'last-refresh' in contact:
                    dt = datetime.datetime.fromisoformat(contact['last-refresh'])
                elif kever and kever.dater:
                    dt = datetime.datetime.fromisoformat(f'{kever.dater.dts}')
                sn = None
                if kever and kever.sner:
                    sn = kever.sn

                title = ft.Text(contact['alias'])
                if dt is not None and sn is not None:
                    title = ft.Text(f'{contact["alias"]}')

                tile = ft.ListTile(
                    leading=ft.Icon(ft.Icons.SQUARE, tooltip='Witness'),
                    title=title,
                    subtitle=ft.Text(contact['id'], font_family='monospace'),
                    trailing=ft.PopupMenuButton(
                        tooltip=None,
                        icon=ft.Icons.MORE_VERT,
                        items=[
                            ft.PopupMenuItem(text='View', icon=ft.Icons.PAGEVIEW, on_click=self.view_witness, data=pre),
                            ft.PopupMenuItem(text='Delete', icon=ft.Icons.DELETE_FOREVER),
                        ],
                    ),
                    on_click=self.view_witness,
                    data=pre,
                    shape=ft.StadiumBorder(),
                )
                self.list.controls.append(
                    ft.Container(
                        content=tile,
                    )
                )
                self.list.controls.append(ft.Divider(opacity=0.1))

        self.update()

    @log_errors
    async def view_witness(self, e):
        self.app.page.route = f'/witnesses/{e.control.data}/view'
        self.app.page.update()

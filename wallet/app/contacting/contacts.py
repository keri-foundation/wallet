import datetime
import logging
import urllib.parse
from urllib.parse import urlparse

import flet as ft
from flet.core import padding
from flet.core.icons import Icons
from keri.app import connecting

from wallet.app.contacting.contact import ContactBase, filter_witnesses

logger = logging.getLogger('wallet')


class Contacts(ContactBase):
    """Contacts page showing all contacts that have had an OOBI resolution performed for."""

    def __init__(self, app):
        self.app = app
        self.list = ft.Column([], spacing=0, expand=True)

        super().__init__(app, ft.Container(content=self.list, padding=padding.only(bottom=125)))

    def did_mount(self):
        self.page.run_task(self.refresh_contacts)

    async def refresh_contacts(self):
        org = connecting.Organizer(hby=self.app.agent.hby)
        await self.set_contacts(org.list())
        self.page.update()

    async def add_contact(self, _):
        self.app.page.route = '/contacts/create'
        self.app.page.update()

    async def set_contacts(self, contacts):
        self.list.controls.clear()
        icon = Icons.PERSON
        tip = 'Contacts'

        contacts = sorted(contacts, key=lambda c: c['alias'])
        contacts = filter_witnesses(contacts)

        if len(contacts) == 0:
            self.list.controls.append(
                ft.Container(
                    content=ft.Text(
                        'No contacts found.',
                    ),
                    padding=ft.padding.all(20),
                )
            )
        else:
            for contact in contacts:
                pre = contact['id']
                kever = self.app.agent.hby.kevers[pre]
                c = urllib.parse.parse_qs(urlparse(contact['oobi']).query)
                if 'tag' in c and 'witness' in c['tag']:
                    continue

                view = ft.PopupMenuItem(
                    text='View',
                    icon=ft.Icons.PAGEVIEW,
                    on_click=self.view_contact,
                )
                view.data = contact

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
                    title = ft.Text(f'{contact["alias"]} (SN: {sn} Datetime: {dt.strftime("%Y-%m-%d %I:%M %p")})')

                tile = ft.ListTile(
                    leading=ft.Icon(icon, tooltip=tip),
                    title=title,
                    subtitle=ft.Text(contact['id'], font_family='monospace'),
                    trailing=ft.PopupMenuButton(
                        tooltip=None,
                        icon=ft.Icons.MORE_VERT,
                        items=[
                            view,
                            ft.PopupMenuItem(text='Delete', icon=ft.Icons.DELETE_FOREVER),
                        ],
                    ),
                    on_click=self.view_contact,
                    data=contact,
                    shape=ft.StadiumBorder(),
                )
                self.list.controls.append(ft.Container(content=tile))
                self.list.controls.append(ft.Divider(opacity=0.1))

        self.update()

    async def view_contact(self, e):
        contact = e.control.data
        self.app.page.route = f'/contacts/{contact["id"]}/view'
        self.app.page.update()

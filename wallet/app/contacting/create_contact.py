import logging
import random
from urllib.parse import urljoin, urlparse

import flet as ft
from keri import kering

from wallet.app.colouring import Colouring
from wallet.app.contacting.contact import ContactBase
from wallet.app.oobing.oobi_resolver import OobiResolver

logger = logging.getLogger('wallet')


class CreateContactPanel(ContactBase):
    def __init__(self, app):
        self.app = app

        self.alias = ft.TextField(label='Alias')
        self.oobi = ft.TextField(label='OOBI', width=400)

        oobis = []
        for pre in self.app.hby.habs:
            oobis.append(self.generate_oobi(pre))
        oobis = [o for oob in oobis for o in oob]

        if len(oobis) > 0:
            o = random.choice(oobis)
            self.my_oobi = ft.Text(
                f'{o}', tooltip=o, width=800, max_lines=3, overflow=ft.TextOverflow.VISIBLE, weight=ft.FontWeight.W_200
            )

            async def copy(e):
                self.app.page.set_clipboard(e.control.data)
                self.app.snack('OOBI URL Copied!', duration=2000)

            self.oobi_copy = ft.IconButton(icon=ft.Icons.COPY_ROUNDED, data=o, on_click=copy)

        self.verified = ft.Icon(ft.Icons.SHIELD_OUTLINED, size=32, color=Colouring.get(Colouring.RED))
        super(CreateContactPanel, self).__init__(app=app, panel=self.panel())

    def generate_oobi(self, e):
        hab = self.app.hby.habByPre(e)

        if not hab.kever.wits:
            return []

        oobis = []
        for wit in hab.kever.wits:
            urls = hab.fetchUrls(eid=wit, scheme=kering.Schemes.http) or hab.fetchUrls(eid=wit, scheme=kering.Schemes.https)
            if not urls:
                return []

            url = urls[kering.Schemes.http] if kering.Schemes.http in urls else urls[kering.Schemes.https]
            up = urlparse(url)
            oobis.append(urljoin(up.geturl(), f'/oobi/{hab.pre}/witness/{wit}'))

        return oobis

    async def create_contact(self, e):
        if self.alias.value == '' or self.oobi.value == '':
            self.app.snack('Missing required field')
            return

        self.app.snack(f'Creating contact {self.alias.value}...')

    def load_witnesses(self):
        return [ft.dropdown.Option(wit['id']) for wit in self.app.witnesses]

    async def callback(self, result):
        logger.info('callback: %s', result)
        self.app.page.route = '/contacts'
        self.app.page.update()

    async def error_callback(self, result):
        pass

    def panel(self):
        orr = OobiResolver(self.app, self.callback, self.error_callback)
        return ft.Container(
            content=ft.Column([ft.Text('Create Contact', size=24), orr.render()]),
            expand=True,
            alignment=ft.alignment.top_left,
            padding=ft.padding.only(left=10, top=15),
        )

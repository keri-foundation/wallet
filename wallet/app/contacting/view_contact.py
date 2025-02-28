"""
view_contact.py - View Contact Panel
"""

import asyncio
import datetime
import logging

import flet as ft
from flet.core import padding
from flet.core.icons import Icons
from keri.app import connecting
from keri.app.habbing import GroupHab
from keri.peer import exchanging
from mnemonic import mnemonic

from wallet.app.colouring import Colouring
from wallet.app.contacting.contact import ContactBase
from wallet.app.oobing.oobi_resolver_service import OOBIResolverService
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class ViewContactPanel(ContactBase):
    def __init__(self, app, contact):
        self.app = app
        self.contact = contact
        self.pre = contact['id']
        self.kever = self.app.agent.hby.kevers[self.pre]
        self.cancelled = False
        self.selected_identifier = None
        self.unverified = ft.Icon(
            Icons.SHIELD_OUTLINED, size=32, color=Colouring.get(Colouring.RED), tooltip='Unverified', visible=True
        )
        self.verified = ft.Icon(ft.Icons.SHIELD_ROUNDED, size=32, tooltip='Verified', visible=True)

        self.phrase = ft.TextField(read_only=True, width=800)
        self.pacifier = ft.Text(italic=True, size=14, weight=ft.FontWeight.W_200)

        self.copy_phrase = ft.IconButton(icon=ft.Icons.COPY_ROUNDED, on_click=self.copy_challenge, visible=False)

        self.alias = self.contact['alias']
        self.verify_challenge_text = ft.TextField(width=800, on_change=self.verify_enable)

        logger.info(f'Loading contact for {self.alias}')

        sn, dt = self.get_sn_date()
        self.sn_text = ft.Text(sn)
        self.dt_text = ft.Text(dt.strftime('%Y-%m-%d %I:%M %p'))

        super(ViewContactPanel, self).__init__(
            app=app,
            panel=self.panel(),
            title=ft.Row(
                controls=[
                    ft.Container(
                        ft.Text(value=f'Alias: {self.alias}', size=24),
                        padding=ft.padding.only(10, 0, 10, 0),
                    ),
                    ft.Container(
                        ft.IconButton(icon=ft.Icons.CLOSE, on_click=self.close),
                        alignment=ft.alignment.top_right,
                        expand=True,
                        padding=ft.padding.only(0, 0, 10, 0),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def get_sn_date(self):
        org = connecting.Organizer(hby=self.app.agent.hby)
        contact = org.get(self.contact['id'])
        dt = None
        if 'last-refresh' in contact:
            dt = datetime.datetime.fromisoformat(contact['last-refresh'])
        elif self.kever and self.kever.dater:
            dt = datetime.datetime.fromisoformat(f'{self.kever.dater.dts}')
        sn = None
        if self.kever and self.kever.sner:
            sn = self.kever.sn

        return sn, dt

    def panel(self):
        options = []

        for hab in self.app.hby.habs:
            options.append(
                ft.dropdown.Option(
                    key=self.app.hby.habs[hab].pre, text=f'{self.app.hby.habs[hab].name} - {self.app.hby.habs[hab].pre}'
                ),
            )

        self.identifiers = ft.Dropdown(
            width=800,
            options=options,
            on_change=self.select_identifier,
        )

        self.verify_button = ft.IconButton(
            icon=ft.Icons.CHECK,
            on_click=self.verify_challenge,
            disabled=True,
        )

        self.verify_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(f'Generate challenge to send to {self.alias}', size=14),
                                        ft.IconButton(icon=ft.Icons.LOOP, on_click=self.generate_challenge),
                                    ]
                                ),
                                ft.Row([self.phrase, self.copy_phrase]),
                                ft.Row([self.pacifier]),
                            ]
                        )
                    ),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(f'Respond to a challenge {self.alias} sent you', size=14),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.verify_challenge_text,
                                    self.verify_button,
                                ]
                            ),
                        ]
                    ),
                ]
            ),
            visible=False,
        )

        self.is_verfied()

        return ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text('Prefix:', weight=ft.FontWeight.BOLD, size=14, width=175),
                            ft.Text(self.contact['id'], font_family='monospace'),
                            self.verified,
                            self.unverified,
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Sequence No.:', weight=ft.FontWeight.BOLD, size=14, width=175),
                            self.sn_text,
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Keystate Updated At:', weight=ft.FontWeight.BOLD, size=14, width=175),
                            self.dt_text,
                        ]
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text('OOBI:', weight=ft.FontWeight.BOLD, size=14, width=175),
                                        ft.Container(
                                            content=ft.Text(  # OOBI URL
                                                value=f'{self.contact["oobi"]}',
                                                tooltip='OOBI URL',
                                                max_lines=3,
                                                overflow=ft.TextOverflow.VISIBLE,
                                                size=14,
                                                weight=ft.FontWeight.W_200,
                                            ),
                                            on_click=self.copy_oobi,
                                            data=self.contact['oobi'],
                                            expand=True,
                                            alignment=ft.alignment.top_right,
                                        ),
                                        ft.IconButton(
                                            icon=ft.Icons.COPY_ROUNDED,
                                            data=self.contact['oobi'],
                                            on_click=self.copy_oobi,
                                            padding=padding.only(right=10),
                                        ),
                                    ]
                                )
                            ]
                        )
                    ),
                    ft.ExpansionTile(
                        title=ft.Text('Details', size=14),
                        affinity=ft.TileAffinity.LEADING,
                        initially_expanded=False,
                        shape=ft.StadiumBorder(),
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Container(
                                        content=ft.Row(
                                            [
                                                ft.Text('Refresh Key State:', width=175, weight=ft.FontWeight.BOLD),
                                                ft.IconButton(
                                                    tooltip='Refresh key state',
                                                    icon=ft.Icons.REFRESH_ROUNDED,
                                                    on_click=self.refresh_keystate,
                                                    padding=padding.only(right=10),
                                                ),
                                            ],
                                        ),
                                        padding=padding.only(top=10),
                                    ),
                                ],
                            )
                        ],
                    ),
                    ft.Divider(),
                    ft.Text('To begin verifying a contact, select an identifier to associate the contact with:', size=14),
                    self.identifiers,
                    self.verify_panel,
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                'Close',
                                on_click=self.close,
                                data=self.app,
                            )
                        ]
                    ),
                ]
            ),
            expand=True,
            alignment=ft.alignment.top_left,
            padding=padding.only(left=10, top=15, bottom=100),
        )

    async def close(self, e):
        self.cancelled = True
        self.app.page.route = '/contacts'
        self.app.page.update()

    async def copy_oobi(self, e):
        self.app.page.set_clipboard(e.control.data)
        self.app.snack('OOBI URL Copied!', duration=2000)

    def is_verfied(self):
        accepted = [saider.qb64 for saider in self.app.hby.db.chas.get(keys=(self.contact['id'],))]
        received = [saider.qb64 for saider in self.app.hby.db.reps.get(keys=(self.contact['id'],))]

        valid = set(accepted) & set(received)

        self.unverified.visible = len(valid) == 0
        self.verified.visible = len(valid) > 0

    async def select_identifier(self, e):
        self.selected_identifier = e.control.value
        self.verify_panel.visible = len(self.selected_identifier) > 0
        await self.generate_challenge(None)
        self.update()

    async def show_verify(self):
        return self.selected_identifier is not None

    async def copy_challenge(self, e):
        sig = self.contact['id']
        self.app.page.set_clipboard(e.control.data)
        self.app.snack('Phrase Copied!')
        await asyncio.sleep(1.0)
        self.app.snack('Waiting for challenge response')

        found = False
        i = 0
        while not found and not self.cancelled:
            self.pacifier.value = f'Waiting for challenge response{"." * i}'
            self.app.page.update()

            saiders = self.app.hby.db.reps.get(keys=(sig,))
            for saider in saiders:
                exn = self.app.hby.db.exns.get(keys=(saider.qb64,))

                if self.phrase.value == ' '.join(exn.ked['a']['words']):
                    found = True
                    self.app.hby.db.chas.add(keys=(sig,), val=saider)
                    break

            if found:
                break

            i += 1
            if i > 3:
                i = 0
            await asyncio.sleep(3)

        if found:
            self.pacifier.value = ''
            self.unverified.visible = False
            self.verified.visible = True
            self.update()

            self.app.snack('Challenge successful.')
            self.app.page.update()

    async def verify_enable(self, e):
        got_mnemonic = len(self.verify_challenge_text.value.split(' ')) == 12

        self.verify_button.disabled = False if got_mnemonic else True
        if got_mnemonic:
            self.verify_button.icon_color = ft.Colors.GREY_400
        self.update()

    async def generate_challenge(self, e):
        del e
        mnem = mnemonic.Mnemonic(language='english')
        self.phrase.value = mnem.generate(strength=128)
        self.copy_phrase.data = self.phrase.value
        self.copy_phrase.visible = True
        self.update()

    async def verify_challenge(self, e):
        hab = self.app.hby.habs[self.selected_identifier]

        if self.identifiers.value is None:
            self.app.snack('Select an identifier to verify with')
            self.app.page.update()
            return

        payload = dict(i=self.selected_identifier, words=self.verify_challenge_text.value.split(' '))

        exn, _ = exchanging.exchange(route='/challenge/response', payload=payload, sender=hab.pre)
        ims = hab.endorse(serder=exn, last=False, pipelined=False)
        del ims[: exn.size]

        senderHab = hab.mhab if isinstance(hab, GroupHab) else hab

        self.app.agent.postman.send(src=senderHab.pre, dest=self.contact['id'], topic='challenge', serder=exn, attachment=ims)

        while not self.app.agent.postman.cues:
            await asyncio.sleep(1.0)

        self.verify_challenge_text.value = ''
        self.app.page.update()
        self.app.snack('Challenge response sent!')

    @log_errors
    async def refresh_keystate(self, e):
        """
        Retrieving current key state for each member of the multisig Hab will push key state
        and other notifications through the system. This method can be removed once notifications
        are pushed through with a different mechanism.
        """
        pre = self.contact['id']
        logger.info(f'Querying key state for contact {pre}')
        logger.info(f'Querying key state for contact {self.contact}')
        await OOBIResolverService(self.app).resolve_oobi(pre=self.contact['id'], oobi=self.contact['oobi'], force=True)
        sn, dt = self.get_sn_date()
        self.sn_text.value = sn
        self.dt_text.value = dt.strftime('%Y-%m-%d %I:%M %p')
        self.update()

"""
view_identifier.py - View Identifier Panel
"""

import asyncio
import base64
import io
import logging
import random
from urllib.parse import urljoin, urlparse

import flet as ft
import qrcode
from flet.core import padding
from keri import kering
from keri.app import habbing
from keri.app.keeping import Algos
from keri.db import dbing

from wallet.app.identifying.identifier import IdentifierBase
from wallet.app.oobing.oobi_resolver_service import OOBIResolverService
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class ViewIdentifierPanel(IdentifierBase):
    def __init__(self, app, hab):
        self.app = app
        self.org = self.app.agent.org
        self.hab = hab
        self.show_key_state_update = False
        self.show_witness_panel = len(self.hab.kever.wits) > 0

        if isinstance(hab, habbing.GroupHab):
            self.typePanel = ft.Row(
                [
                    ft.Text('Key Type:', weight=ft.FontWeight.BOLD, width=175),
                    ft.Text(
                        'Group Multisig Identifier',
                    ),
                ],
            )
            self.show_key_state_update = True
        elif isinstance(hab, habbing.Hab):  # GroupHab does not have .algo prop
            if hab.algo == Algos.salty:
                self.typePanel = ft.Row(
                    [
                        ft.Text(
                            'Key Type:',
                            width=175,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            'Hierarchical Key Chain Identifier',
                        ),
                    ]
                )
            elif hab.algo == Algos.randy:
                self.typePanel = ft.Row(
                    [
                        ft.Text(
                            'Key Type:',
                            width=175,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            'Random Key Generation Identifier',
                        ),
                    ]
                )
        else:
            logger.error(f'Unknown hab type: {type(hab)}')
            raise ValueError(f'Unknown hab type: {type(hab)}')

        self.publicKeys = ft.Column()
        for idx, verfer in enumerate(self.hab.kever.verfers):
            self.publicKeys.controls.append(
                ft.Row(
                    [
                        ft.Text(str(idx + 1)),
                        ft.Text(verfer.qb64, font_family='monospace'),
                    ]
                )
            )

        self.oobiTabs = ft.Column()
        self.oobi_qr = ft.Image(
            src='',
        )
        self.oobi_url = ft.Text('')
        self.oobi_copy = ft.IconButton()

        self.resubmit_button = ft.ElevatedButton(
            'Resubmit',
            on_click=self.resubmit,
        )
        ser = self.hab.kever.serder

        self.submit_progress = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2,
        )
        self.submit_refresh_row = ft.Row(
            [
                ft.Text('Waiting for receipts...'),
                self.submit_progress,
            ],
            visible=False,
        )

        self.resubmit_button.visible = len(self.hab.kever.wits) != len(
            self.hab.db.getWigs(
                # Only show if witness receipts are missing
                dbing.dgKey(ser.preb, ser.saidb)
            )
        )
        self.generate_oobi(kering.Roles.witness)

        super(ViewIdentifierPanel, self).__init__(
            app,
            self.panel(),
            ft.Row(
                controls=[
                    ft.Container(
                        ft.Text(value=f'Alias: {self.hab.name}', size=24),
                        padding=ft.padding.only(10, 0, 10, 0),
                    ),
                    ft.Container(
                        ft.IconButton(icon=ft.Icons.CLOSE, on_click=self.cancel),
                        alignment=ft.alignment.top_right,
                        expand=True,
                        padding=ft.padding.only(0, 0, 10, 0),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def generate_oobi(self, e):
        oobis = self.loadOOBIs(e)

        if len(oobis) == 0:
            return 0

        oobi = random.choice(oobis)
        img = qrcode.make(oobi)
        f = io.BytesIO()
        img.save(f)
        f.seek(0)

        async def copy(e):
            self.app.page.set_clipboard(e.control.data)
            self.app.snack('OOBI URL Copied!', duration=2000)

        self.oobi_qr = ft.Image(src_base64=base64.b64encode(f.read()).decode('utf-8'), width=175)
        self.oobi_url = ft.Container(
            content=ft.Text(
                value=oobi,
                tooltip=oobi,
                max_lines=3,
                size=12,
                overflow=ft.TextOverflow.VISIBLE,
                weight=ft.FontWeight.W_200,
                width=600,
            ),
            on_click=copy,
            data=oobi,
        )
        self.oobi_copy = ft.IconButton(icon=ft.Icons.COPY_ROUNDED, data=oobi, on_click=copy, tooltip='Copy OOBI')

        self.oobiTabs.controls.clear()
        self.oobiTabs.controls.append(
            ft.Column(
                [
                    ft.Row([self.oobi_url, self.oobi_copy]),
                    ft.Row([self.oobi_qr]),
                    ft.Container(padding=ft.padding.only(top=6)),
                ]
            )
        )

        return len(oobis)

    async def reset_oobi(self):
        self.oobiTabs.controls.clear()
        self.update()

    async def layout_oobi(self, e):
        if not self.generate_oobi(e.data):
            self.app.snack(f'No {e} OOBIs', duration=2000)
        self.update()

    @log_errors
    async def refresh_keystate(self, e):
        """
        Retrieving current key state for each member of the multisig Hab will push key state
        and other notifications through the system. This method can be removed once notifications
        are pushed through with a different mechanism.
        """
        logger.info(self.hab.smids)
        for pre in self.hab.smids:
            if pre in self.app.agent.hby.habs.keys():
                continue  # Don't query key state for self because it's already in the agent
            contact = self.org.get(pre)
            logger.info(f'Querying key state for {contact["alias"]} with pre: {pre}')
            await OOBIResolverService(self.app).resolve_oobi(pre=pre, oobi=contact['oobi'], force=True)
            self.app.snack(f"Resolved {contact['alias']}'s key state for AID {pre}")

    async def resubmit(self, _):
        self.app.agent.witness_resubmit(self.hab.pre)
        self.app.snack(f'Resubmitting {self.hab.pre} for witness receipts.')
        self.resubmit_button.visible = False
        self.submit_refresh_row.visible = True
        self.page.update()

        updated = False
        while not updated:
            updated = len(self.hab.kever.wits) == len(
                self.hab.db.getWigs(dbing.dgKey(self.hab.kever.serder.preb, self.hab.kever.serder.saidb))
            )
            await asyncio.sleep(1)

        if updated:
            self.submit_refresh_row.visible = False
            self.app.snack(f'Received all witness receipts for {self.hab.pre}.')
            self.page.update()
        else:
            self.app.snack(f'Failed to receive witness receipts for {self.hab.pre}.')
            self.page.update()
            self.submit_refresh_row.visible = False
            self.resubmit_button.visible = True

    async def rotate_identifier(self, e):
        del e
        """Navigate to the single sig rotate panel."""
        hab = self.hab
        self.app.page.route = f'/identifiers/{hab.pre}/rotate'
        self.app.page.update()

    async def cancel(self, e):
        self.app.page.route = '/identifiers'
        self.app.page.update()

    async def cb_copy_digest(self, e):
        """copies the latest event digest to clipboard"""
        self.app.page.set_clipboard(e.control.data)
        self.app.snack('Event Digest Copied!', duration=2000)

    async def cb_copy_sn(self, e):
        """copies the latest event sequence number to clipboard"""
        self.app.page.set_clipboard(f'{e.control.data}')
        self.app.snack('Sequence Number Copied!', duration=2000)

    def panel(self):
        kever = self.hab.kever
        ser = kever.serder
        dgkey = dbing.dgKey(ser.preb, ser.saidb)
        wigs = self.hab.db.getWigs(dgkey)

        return ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text('Prefix:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(self.hab.pre, font_family='monospace'),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Sequence Number:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Container(content=ft.Text(kever.sner.num), on_click=self.cb_copy_sn, data=kever.sner.num),
                            ft.IconButton(
                                icon=ft.Icons.COPY_ROUNDED,
                                data=kever.sner.num,
                                on_click=self.cb_copy_sn,
                                tooltip='Copy Sequence Number',
                            ),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Event digest:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Container(
                                content=ft.Text(kever.serder.ked['d'], font_family='monospace'),
                                on_click=self.cb_copy_digest,
                                data=kever.serder.ked['d'],
                            ),
                            ft.IconButton(
                                icon=ft.Icons.COPY_ROUNDED,
                                data=kever.serder.ked['d'],
                                on_click=self.cb_copy_digest,
                                tooltip='Copy Digest',
                            ),
                        ]
                    ),
                    self.typePanel,
                    ft.Column(
                        controls=[
                            ft.Row(
                                [
                                    ft.Text('Refresh Key State:', width=175, weight=ft.FontWeight.BOLD),
                                    ft.IconButton(
                                        tooltip='Refresh key state',
                                        icon=ft.Icons.REFRESH_ROUNDED,
                                        on_click=self.refresh_keystate,
                                        padding=padding.only(right=10),
                                    ),
                                ]
                            ),
                        ],
                        visible=self.show_key_state_update,
                    ),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        'Establishment Only',
                                        weight=ft.FontWeight.BOLD,
                                        width=168,
                                        size=14,
                                    ),
                                    ft.Checkbox(
                                        value=True,
                                        disabled=True,
                                    ),
                                ],
                                visible=kever.estOnly,
                            ),
                            ft.Row(
                                [
                                    ft.Text(
                                        'Do Not Delegate',
                                        weight=ft.FontWeight.BOLD,
                                        width=168,
                                        size=14,
                                    ),
                                    ft.Checkbox(
                                        value=True,
                                        disabled=True,
                                    ),
                                ],
                                visible=kever.estOnly,
                            ),
                        ]
                    ),
                    ft.Divider(),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        'Witnesses:',
                                        weight=ft.FontWeight.BOLD,
                                        width=175,
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Text('Count:', weight=ft.FontWeight.BOLD, width=175),
                                    ft.Text(str(len(self.hab.kever.wits))),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Text('Receipt:', weight=ft.FontWeight.BOLD, width=175),
                                    ft.Text(str(len(wigs))),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Text(
                                        'Threshold:',
                                        weight=ft.FontWeight.BOLD,
                                        width=175,
                                    ),
                                    ft.Text(kever.toader.num),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Column(
                                        controls=[
                                            self.resubmit_button,
                                            self.submit_refresh_row,
                                        ]
                                    ),
                                ]
                            ),
                        ],
                        visible=self.show_witness_panel,
                    ),
                    ft.Divider(visible=self.show_witness_panel),
                    ft.Row(
                        [
                            ft.Text('Public Keys:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Row(
                                controls=[
                                    ft.IconButton(
                                        icon=ft.Icons.ROTATE_LEFT_ROUNDED,
                                        on_click=self.rotate_identifier,
                                        tooltip='Rotate',
                                    )
                                ]
                            ),
                        ]
                    ),
                    ft.Container(content=self.publicKeys, padding=ft.padding.only(left=40)),
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.Text('Generate OOBI:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Dropdown(
                                options=[
                                    ft.dropdown.Option(
                                        key=kering.Roles.controller,
                                        text=kering.Roles.controller.capitalize(),
                                    ),
                                    ft.dropdown.Option(
                                        key=kering.Roles.mailbox,
                                        text=kering.Roles.mailbox.capitalize(),
                                    ),
                                    ft.dropdown.Option(
                                        key=kering.Roles.witness,
                                        text=kering.Roles.witness.capitalize(),
                                    ),
                                ],
                                value=kering.Roles.witness,
                                on_change=self.layout_oobi,
                            ),
                        ]
                    ),
                    ft.Container(
                        content=self.oobiTabs,
                    ),
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                'Close',
                                on_click=self.close,
                            )
                        ]
                    ),
                ],
                scroll=ft.ScrollMode.ALWAYS,
            ),
            expand=True,
            alignment=ft.alignment.top_left,
            padding=padding.only(left=10, bottom=80),
        )

    def loadOOBIs(self, role):
        if role in (kering.Roles.witness,):  # Fetch URL OOBIs for all witnesses
            oobis = []
            for wit in self.hab.kever.wits:
                urls = self.hab.fetchUrls(eid=wit, scheme=kering.Schemes.http) or self.hab.fetchUrls(
                    eid=wit, scheme=kering.Schemes.https
                )
                if not urls:
                    return []

                url = urls[kering.Schemes.http] if kering.Schemes.http in urls else urls[kering.Schemes.https]
                up = urlparse(url)
                oobis.append(urljoin(up.geturl(), f'/oobi/{self.hab.pre}/witness/{wit}'))

            return oobis

        elif role in (kering.Roles.controller,):  # Fetch any controller URL OOBIs
            oobis = []
            urls = self.hab.fetchUrls(eid=self.hab.pre, scheme=kering.Schemes.http) or self.hab.fetchUrls(
                eid=self.hab.pre, scheme=kering.Schemes.https
            )
            if not urls:
                return []

            url = urls[kering.Schemes.http] if kering.Schemes.http in urls else urls[kering.Schemes.https]
            up = urlparse(url)
            oobis.append(urljoin(up.geturl(), f'/oobi/{self.hab.pre}/controller'))
            return oobis

        elif role in (kering.Roles.agent,):
            oobis = []
            roleUrls = self.hab.fetchRoleUrls(
                self.hab.pre, scheme=kering.Schemes.http, role=kering.Roles.agent
            ) or self.hab.fetchRoleUrls(self.hab.pre, scheme=kering.Schemes.https, role=kering.Roles.agent)
            if not roleUrls:
                return []

            for eid, urls in roleUrls['agent'].items():
                url = urls[kering.Schemes.http] if kering.Schemes.http in urls else urls[kering.Schemes.https]
                up = urlparse(url)
                oobis.append(urljoin(up.geturl(), f'/oobi/{self.hab.pre}/agent/{eid}'))

            return oobis

        return []

    async def close(self, _):
        self.app.page.route = '/identifiers'
        self.app.page.update()

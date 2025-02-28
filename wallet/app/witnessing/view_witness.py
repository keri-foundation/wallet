"""
view_witness.py - View Witness Panel
"""

import datetime
import logging

import flet as ft
from flet.core import padding
from keri.app import connecting

from wallet.app.witnessing.witness import WitnessBase

logger = logging.getLogger('wallet')


class ViewWitness(WitnessBase):
    def __init__(self, app, witness):
        self.app = app
        self.witness = witness
        self.pre = witness['id']
        self.kever = self.app.agent.hby.kevers[self.pre]
        self.cancelled = False

        self.alias = self.witness['alias']

        logger.info(f'Loading witness for {self.alias}')

        sn, dt = self.get_sn_date()
        self.sn_text = ft.Text(sn)
        self.dt_text = ft.Text(dt.strftime('%Y-%m-%d %I:%M %p'))

        super(ViewWitness, self).__init__(
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
        witness = org.get(self.witness['id'])
        dt = None
        if 'last-refresh' in witness:
            dt = datetime.datetime.fromisoformat(witness['last-refresh'])
        elif self.kever and self.kever.dater:
            dt = datetime.datetime.fromisoformat(f'{self.kever.dater.dts}')
        sn = None
        if self.kever and self.kever.sner:
            sn = self.kever.sn

        return sn, dt

    def panel(self):
        options = []

        aids = set()

        for hab in self.app.hby.habs:
            options.append(
                ft.dropdown.Option(
                    key=self.app.hby.habs[hab].pre, text=f'{self.app.hby.habs[hab].name} - {self.app.hby.habs[hab].pre}'
                ),
            )
            if self.witness in self.app.hby.habs[hab].kever.wits:
                aids.add(hab)

        print(aids)

        return ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text('Prefix:', weight=ft.FontWeight.BOLD, size=14),
                            ft.Text(self.witness['id'], font_family='monospace'),
                        ]
                    ),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text('OOBI:', weight=ft.FontWeight.BOLD, size=14),
                                        ft.Text(  # OOBI URL
                                            value=f'{self.witness["oobi"]}',
                                            tooltip='OOBI URL',
                                            max_lines=3,
                                            overflow=ft.TextOverflow.VISIBLE,
                                            size=14,
                                            weight=ft.FontWeight.W_200,
                                        ),
                                        ft.IconButton(
                                            icon=ft.Icons.COPY_ROUNDED,
                                            data=self.witness['oobi'],
                                            on_click=self.copy_oobi,
                                            padding=padding.only(right=10),
                                        ),
                                    ]
                                )
                            ]
                        )
                    ),
                    ft.Divider(),
                    ft.Text('Witness for:', size=14),
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
        self.app.page.route = '/witnesses'
        self.app.page.update()

    async def copy_oobi(self, e):
        self.app.page.set_clipboard(e.control.data)
        self.app.snack('OOBI URL Copied!', duration=2000)

    async def select_identifier(self, e):
        self.selected_identifier = e.control.value
        self.update()

    async def show_verify(self):
        return self.selected_identifier is not None

"""
rotate_identifier.py
"""

import logging

import flet as ft
from flet.core.types import FontWeight
from keri.app import connecting

from wallet.app.identifying.identifier import IdentifierBase

logger = logging.getLogger('wallet')


class RotateIdentifierPanel(IdentifierBase):
    def __init__(self, app, hab):
        self.app = app
        self.hab = hab

        kever = self.hab.kever
        self.org = connecting.Organizer(hby=app.agent.hby)
        self.isith = ft.TextField(
            value=kever.tholder.sith,
        )
        self.nsith = ft.TextField(
            value=kever.ntholder.sith,
        )
        self.ncount = ft.TextField(
            value=f'{len(kever.ndigers)}',
        )
        self.toad = ft.TextField(
            value=kever.toader.num,
        )

        self.witnesses = self.loadWitnesses(self.app)

        self.witnessList = ft.Column()
        self.witnessDropdown = ft.Dropdown(
            options=self.witnesses,
            expand=True,
            text_size=14,
            text_style=ft.TextStyle(font_family='monospace'),
        )

        self.witnessSelectorRow = ft.Row(
            controls=[
                self.witnessDropdown,
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    tooltip='Add Witness',
                    on_click=self.addWitness,
                ),
            ],
        )

        super(RotateIdentifierPanel, self).__init__(
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

    def loadWitnesses(self, app):
        return [
            ft.dropdown.Option(
                key=wit['id'],
                text=f'{wit["alias"]} | {wit["id"]}' if wit['alias'] else f'{wit["id"]}',
                data=(wit['id'], wit['alias']),
            )
            for wit in app.witnesses
        ]

    async def rotateee(self, _):
        self.hab.rotate(
            isith=self.isith.value,
            nsith=self.nsith.value,
            ncount=int(self.ncount.value),
            toad=self.toad.value,
        )

        if self.hab.delpre:
            self.app.agent.anchors.push(dict(sn=self.hab.kever.sner.num))
            self.app.snack(f'Rotating {self.hab.pre}, waiting for delegation approval...')

        elif len(self.hab.kever.wits) > 0:
            self.app.agent.witners.push(dict(serder=self.hab.kever.serder))
            self.app.snack(f'Rotating {self.hab.pre}, waiting for witness receipts...')

        self.app.page.route = f'/identifiers/{self.hab.pre}/view'
        self.app.page.update()

    async def cancel(self, _):
        self.app.page.route = '/identifiers'
        self.app.page.update()

    async def back_to_identifier(self, e):
        self.app.page.route = f'/identifiers/{self.hab.pre}/view'
        self.app.page.update()

    def witnessTile(self, wit_ct, on_delete):
        """
        Parameters:
            wit_ct (dict): contact entry of the witness
        """
        if 'alias' in wit_ct:
            title = ft.Column([ft.Text(wit_ct['alias']), ft.Text(wit_ct['id'], font_family='monospace')])
        else:
            title = ft.Text(wit_ct['id'], font_family='monospace')
        return ft.ListTile(
            title=title,
            trailing=ft.IconButton(
                ft.Icons.DELETE_OUTLINED,
                on_click=on_delete,
                data=wit_ct['id'],
            ),
            data=wit_ct['id'],
        )

    def findSelectedWitness(self, aid):
        for tile in self.witnessList.controls:
            if tile.data == aid:
                return tile

        return None

    @staticmethod
    def recommendedThold(numWits):
        match numWits:
            case 0:
                return 0
            case 1:
                return 1
            case 2 | 3:
                return 2
            case 4:
                return 3
            case 5 | 6:
                return 4
            case 7:
                return 5
            case 8 | 9:
                return 7
            case 10:
                return 8

    async def deleteWitness(self, e):
        aid = e.control.data
        if (tile := self.findSelectedWitness(aid)) is not None:
            self.witnessList.controls.remove(tile)

        self.toad.value = str(self.recommendedThold(len(self.witnessList.controls)))
        self.toad.update()
        self.witnessList.update()

    async def addWitness(self, _):
        if not self.witnessDropdown.value:
            self.app.snack('Please select a witness')
            return
        witness = self.witnessDropdown.value
        self.witnessDropdown.value = None

        if self.findSelectedWitness(witness) is not None:
            self.app.snack(f'You can not add {witness} more than once')
            return

        witness = self.org.get(witness)
        self.witnessList.controls.append(self.witnessTile(witness, self.deleteWitness))

        self.toad.value = str(self.recommendedThold(len(self.witnessList.controls)))
        self.toad.update()

        self.witnessDropdown.update()
        self.witnessList.update()

    def panel(self):
        kever = self.hab.kever

        return ft.Container(
            content=ft.Column(
                [
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.Text('Prefix:', weight=ft.FontWeight.BOLD),
                            ft.Text(self.hab.pre, font_family='monospace'),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Sequence Number:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(kever.sner.num),
                        ]
                    ),
                    ft.ExpansionTile(
                        title=ft.Text('Witness configuration'),
                        affinity=ft.TileAffinity.LEADING,
                        initially_expanded=False,
                        controls=[
                            self.witnessList,
                            self.witnessSelectorRow,
                        ],
                        expand=True,
                    ),
                    ft.ExpansionTile(
                        title=ft.Text('Key configuration'),
                        affinity=ft.TileAffinity.LEADING,
                        initially_expanded=False,
                        controls=[
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Current signing threshold',
                                                        weight=FontWeight.BOLD,
                                                        width=275,
                                                    ),
                                                    self.isith,
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Next signing threshold',
                                                        weight=FontWeight.BOLD,
                                                        width=275,
                                                    ),
                                                    self.nsith,
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Count',
                                                        weight=FontWeight.BOLD,
                                                        width=275,
                                                    ),
                                                    self.ncount,
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Toad',
                                                        weight=FontWeight.BOLD,
                                                        width=275,
                                                    ),
                                                    self.toad,
                                                ]
                                            ),
                                        ]
                                    ),
                                ]
                            ),
                        ],
                    ),
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                'Rotate',
                                on_click=self.rotateee,
                            ),
                            ft.ElevatedButton(
                                'Cancel',
                                on_click=self.cancel,
                            ),
                        ]
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=True,
            alignment=ft.alignment.top_left,
        )

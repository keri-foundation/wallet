"""
Witnesses module for the Wallet application.
"""

import logging

import flet as ft
from keri.app import connecting

from wallet.app.oobing.oobi_resolver import OobiResolver
from wallet.app.witnessing.witness import WitnessBase

logger = logging.getLogger('wallet')


class AddWitness(WitnessBase):
    def __init__(self, app):
        self.app = app

        super(AddWitness, self).__init__(app=app, panel=self.panel())

    async def callback(self, result):
        logger.info('callback: %s', result)

        roobi = self.app.hby.db.roobi.get(keys=(result,))
        org = connecting.Organizer(hby=self.app.agent.hby)
        org.update(roobi.cid, {'type': 'witness'})

        self.app.page.route = '/witnesses'
        self.app.page.update()

    async def error_callback(self, result):
        pass

    async def cancel(self, e):
        self.app.page.route = '/witnesses'
        self.app.page.update()

    def panel(self):
        orr = OobiResolver(self.app, self.callback, self.error_callback)

        return ft.Container(
            content=ft.Column([ft.Text('Add Witness', size=24), orr.render()]),
        )

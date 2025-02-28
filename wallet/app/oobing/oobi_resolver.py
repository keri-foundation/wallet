import inspect
import logging

import flet as ft

from wallet.app.colouring import Colouring
from wallet.app.component import Component
from wallet.app.oobing.oobi_resolver_service import OOBIResolverService
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class OobiResolver(Component):
    """
    Resolves an OOBI with the connected Agent's Habery
    and then calls a callback with the result, true or false
    """

    def __init__(self, app, callback=None, err_cb=None):
        """
        Creates the controls for resolving an oobi.

        Args:
            app(App): The app that will be used to resolve the oobi
            callback(function): The function that will be called with the result

        Attributes:
            app(App): The app that will be used to resolve the oobi
            callback(function): Function called with the success result if the service succeeds
            err_cb(function): Function called with the error result if the service fails
            alias(str): The alias to resolve
            oobi(str): The oobi to resolve
            txt_alias(TextField): The TextField for the alias
            txt_oobi(TextField): The TextField for the oobi
            svc(OOBIResolverService): The service that will resolve the oobi
        """
        super().__init__()
        self.app = app
        self.callback = callback
        self.err_cb = err_cb

        self.alias = ''
        self.oobi = ''

        def txt_alias_change(e):
            self.alias = e.data

        async def txt_alias_focus(e):
            del e
            self.txt_alias.border_color = None
            self.app.page.update()

        self.txt_alias = ft.TextField(label='Alias', width=400, on_change=txt_alias_change, on_focus=txt_alias_focus)

        def txt_oobi_change(e):
            self.oobi = e.data

        async def txt_oobi_focus(e):
            del e
            self.txt_oobi.border_color = None
            self.app.page.update()

        self.txt_oobi = ft.TextField(label='OOBI', width=400, on_change=txt_oobi_change, on_focus=txt_oobi_focus)

        self.svc = OOBIResolverService(self.app)

    @log_errors
    async def validate(self, e):
        del e
        valid = self.alias != '' and self.oobi != ''
        failed_on = []
        if self.alias == '':
            failed_on.append('Alias')
            self.txt_alias.border_color = Colouring.get(Colouring.RED)

        if self.oobi == '':
            failed_on.append('OOBI')
            self.txt_oobi.border_color = Colouring.get(Colouring.RED)

        if valid:
            self.app.snack(f'Resolving {self.alias}...')
            if await self.svc.resolve_oobi(oobi=self.oobi, alias=self.alias):
                await self.on_service_success()
            else:
                await self.on_service_fail()
        else:
            self.app.snack(
                f'Missing field{"s"[: len(failed_on) ^ 1]}: {", ".join(failed_on[:])}',
            )

    def render(self):
        return ft.Column(
            [
                self.txt_alias,
                self.txt_oobi,
                ft.Container(
                    content=ft.Divider(color=Colouring.get(Colouring.SECONDARY)),
                ),
                ft.Row(
                    [
                        ft.ElevatedButton('Connect', on_click=self.validate),
                        ft.ElevatedButton(
                            'Cancel',
                            on_click=self.on_cancel,
                        ),
                    ]
                ),
            ]
        )

    @property
    def service(self):
        return self.svc

    @log_errors
    async def on_service_success(self):
        self.app.snack(f'{self.alias} resolved')
        self.reset()
        if self.callback is not None:
            if inspect.iscoroutinefunction(self.callback):
                await self.callback(self.oobi)
            else:
                self.callback(self.oobi)

    @log_errors
    async def on_service_fail(self):
        self.app.snack('Failed to resolve OOBI')
        if self.err_cb is not None:
            if inspect.iscoroutinefunction(self.err_cb):
                await self.err_cb(True)
            else:
                self.err_cb(True)

    @log_errors
    async def on_cancel(self, e):
        del e
        self.reset()
        self.app.page.route = '/contacts'
        self.app.page.update()

    def reset(self):
        self.txt_alias.value = ''
        self.txt_oobi.value = ''

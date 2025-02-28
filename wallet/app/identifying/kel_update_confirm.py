import asyncio
import datetime
import logging

import flet as ft
from keri.help import helping

from wallet.app.identifying.identifier import IdentifierBase
from wallet.core.agent_events import AgentEventTypes

logger = logging.getLogger('wallet')


class KELUpdateConfirmDialog(ft.AlertDialog):
    """
    Presents a dialog with a digest and sequence number input for the controller to confirm an update to a KEL.
    """

    def __init__(self, app):
        self.app = app
        self.hab = None
        self.aid_update = None
        self.digest = ft.TextField(
            label='New Digest',
            text_style=ft.TextStyle(font_family='monospace'),
        )
        self.serial = ft.TextField(label='New Sequence Number')
        self.error_text = ft.Text(
            value='',
            visible=False,
        )
        self.update_progress_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
        self.close_task = None
        super(KELUpdateConfirmDialog, self).__init__(
            modal=True,
            title=ft.Text('Update KEL'),
            content=ft.Column(
                controls=[ft.Divider(), self.digest, self.serial, ft.Divider(), self.error_text, self.update_progress_ring],
                height=240,
                width=300,
            ),
            actions=[
                ft.OutlinedButton(text='Cancel', on_click=self.close_confirm),
                ft.ElevatedButton(text='Confirm', on_click=self.confirm_update),
            ],
        )
        self.can_timeout = False

    def will_unmount(self):
        print('Unmounting dialog')

    async def finish_confirm(self, event_type, timeout=15):
        """Event handler for KEL_UPDATE_COMPLETE"""
        start_time = helping.nowUTC()
        timeout_delta = datetime.timedelta(seconds=timeout)
        while True:
            now = helping.nowUTC()
            if now > start_time + timeout_delta:
                logger.info('KEL update timed out')
                self.app.snack('Update request timed out', duration=3000)
                await self.close_confirm(None)
            for event in self.app.agent_events:
                if event['event_type'] == event_type:
                    self.app.agent_events.remove(event)
                    self.app.snack('Update Log complete', duration=3000)
                    logger.info('KEL update complete')
                    await self.close_confirm(None)
            await asyncio.sleep(0.5)

    async def open_confirm(self, hab, aid_update):
        """
        Opens dialog
        """
        self.hab = hab
        self.aid_update = aid_update
        self.open = True
        self.app.page.update()

    async def update_identifier_page(self):
        if self.app.layout.active_view == self.app.layout.identifiers:
            identifiers = self.app.layout.identifiers
            await identifiers.refresh_identifiers()
            await self.app.page.dialog.close_confirm()
            self.app.page.dialog = None
            self.app.page.update()

    async def close_confirm(self, _):
        """
        Closes dialog
        """
        self.open = False
        self.close_task.cancel()
        self.app.page.run_task(self.update_identifier_page)
        self.page.update()

    async def show_error(self, message):
        """
        Shows error message
        """
        self.error_text.value = message
        self.error_text.visible = True
        self.page.update()
        self.app.snack(message, duration=3000)

    async def hide_error(self):
        """
        Hides error message
        """
        self.error_text.value = ''
        self.error_text.visible = False
        self.page.update()

    async def confirm_update(self, e):
        """
        Updates a local AID, usually multisig, from the specified witness
        Parameters:
            e (flet.ControlEvent): The button control triggering this update
        """
        await self.hide_error()
        self.update_progress_ring.visible = True
        self.page.update()

        # new_digest = self.hab.kever.serder.ked['d']
        new_digest = self.aid_update.said
        input_digest = self.digest.value
        new_sn = self.aid_update.sn
        try:
            input_sn = int(self.serial.value)
        except ValueError:
            await self.show_error('Sequence Number must be an integer')
            return

        if new_digest != input_digest:
            await self.show_error('Event Digest does not match latest query, cannot update.')
            return
        if new_sn != input_sn:
            await self.show_error('Sequence Number does not match latest query, cannot update.')
            return
        update_message = (
            f'Updating identifier {self.hab.name} | {self.aid_update.aid} to event {new_sn} with digest {new_digest}'
        )
        self.app.snack(update_message, duration=3000)
        logger.info(update_message)
        self.app.agent.update_reqs.push(self.aid_update)
        self.update_progress_ring.visible = True
        self.close_task = asyncio.create_task(self.finish_confirm(AgentEventTypes.KEL_UPDATE_COMPLETE.value, timeout=30))


class KELUpdateConfirmPanel(IdentifierBase):
    def __init__(self, app, hab, aid_updates):
        self.app = app
        self.hab = hab
        self.aid_updates = list(filter(lambda u: u.aid == hab.pre, aid_updates))
        if not self.aid_updates:
            logger.error(f'No AID updates found for {hab.pre}')
            self.app.snack = ft.SnackBar(ft.Text('No AID updates found for this identifier'), duration=3000)
            self.app.page.route = '/identifiers'

        self.aid_update = self.aid_updates[0]

        super(KELUpdateConfirmPanel, self).__init__(
            app,
            self.panel(),
            ft.Row(
                controls=[
                    ft.Container(
                        ft.Text(value=f'Updating KEL for Alias: {self.hab.name}', size=24),
                        padding=ft.padding.only(10, 20, 10, 0),
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

    def panel(self):
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        [
                            ft.Text('Existing Event Digest:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(self.hab.kever.serder.ked['d'], font_family='monospace'),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('New Event Digest:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(self.aid_update.said, font_family='monospace'),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('Existing Sequence Number:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(f'{self.hab.kever.sn}'),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text('New Sequence Number:', weight=ft.FontWeight.BOLD, width=175),
                            ft.Text(f'{self.aid_update.sn}'),
                        ]
                    ),
                    ft.Divider(),
                    ft.OutlinedButton(text='Confirm Update', on_click=self.confirm_update),
                ]
            ),
            expand=True,
            alignment=ft.alignment.top_left,
        )

    async def cancel(self, e):
        self.app.page.route = '/identifiers'
        self.app.page.update()

    async def confirm_update(self, e):
        """
        Updates a local AID, usually multisig, from the specified witness
        Parameters:
            e (flet.ControlEvent): The button control triggering this update
        """
        logger.info(f'Updating AID {self.hab.name} {self.aid_update.aid}')
        self.app.agent.update_reqs.push(self.aid_update)
        self.app.page.route = '/identifiers'

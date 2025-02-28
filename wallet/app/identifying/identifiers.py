"""
Identifiers module for the Wallet application.
"""

import logging

import flet as ft
from flet.core.icons import Icons
from keri.app import habbing

from wallet.app import colouring
from wallet.app.identifying.identifier import IdentifierBase
from wallet.app.identifying.kel_update_confirm import KELUpdateConfirmDialog
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class Identifiers(IdentifierBase):
    """
    Class representing identifiers in the application.

    Attributes:
        page (ft.Page): The page object associated with the app.
        list (ft.Column): The column object representing the list of identifiers.
    """

    def __init__(self, app):
        self.page: ft.Page = app.page
        self.list = ft.Column([], spacing=0, expand=True)
        self.kel_update_dialog = None

        super().__init__(app, ft.Container(content=self.list, padding=ft.padding.only(bottom=125)))

    def did_mount(self):
        self.page.run_task(self.refresh_identifiers)

    @staticmethod
    def get_habs(agent):
        """Get the Hab instances an agent has."""
        return agent.hby.habs.values()

    @staticmethod
    def get_aids(agent):
        """Get the identifiers (AID prefixes) an agent has."""
        return [hab.pre for hab in Identifiers.get_habs(agent)]

    async def refresh_identifiers(self):
        """
        Refreshes the identifiers by setting them to the current list of HABs and updating the state.
        """
        await self.set_identifiers(self.get_habs(self.app.agent))
        self.update()

    async def add_identifier(self, _):
        """
        Adds an identifier to the application.

        This method sets the route to "/identifiers/create" and updates the page asynchronously.

        Parameters:
        - _: Placeholder parameter (unused)

        Returns:
        - None
        """
        self.app.page.route = '/identifiers/create'
        self.app.page.update()

    def check_aid_updates(self, pre):
        for update in self.app.agent.aid_updates:
            if update.aid == pre:
                return True, update
        return False, None

    @log_errors
    async def kel_update(self, e):
        """
        Updates a local AID, usually multisig, from the specified witness
        Parameters:
            aid_update (AidKelUpdate): Contains the AID and witness information needed to update the
                local KEL from the witness specified
        """
        hab, aid_update = e.control.data
        dialog = KELUpdateConfirmDialog(self.app)
        self.page.dialog = dialog
        await dialog.open_confirm(hab, aid_update)

    @log_errors
    async def set_identifiers(self, habs):
        """
        Sets the identifiers for the given list of habs.

        Args:
            habs (list): A list of habs to set identifiers for.

        Returns:
            None
        """
        self.list.controls.clear()

        if len(habs) == 0:
            self.list.controls.append(
                ft.Container(
                    content=ft.Text(
                        'No identifiers found.',
                    ),
                    padding=ft.padding.all(20),
                )
            )
        else:
            for hab in habs:
                needs_update, aid_update = self.check_aid_updates(hab.pre)
                tip = 'Identifier'

                if isinstance(hab, habbing.GroupHab):
                    icon = Icons.DATASET_LINKED_OUTLINED
                elif isinstance(hab, habbing.Hab):  # GroupHab does not have .algo prop
                    icon = Icons.LINK_OUTLINED
                else:
                    logger.error('Unknown hab type: %s', type(hab))
                    raise ValueError(f'Unknown hab type: {type(hab)}')

                # Bug in FLET that doesn't set `data` in constructor
                view = ft.PopupMenuItem(text='View', icon=ft.Icons.PAGEVIEW, on_click=self.view_identifier)
                view.data = hab
                rotate = ft.PopupMenuItem(
                    text='Rotate',
                    icon=ft.Icons.ROTATE_RIGHT,
                    on_click=self.rotate_identifier,
                )
                rotate.data = hab
                delete = ft.PopupMenuItem(
                    text='Delete',
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=self.delete_identifier,
                )
                delete.data = hab

                title_row = ft.Row(
                    [
                        ft.Text(
                            hab.pre,
                            font_family='monospace',
                        ),
                    ]
                )
                if needs_update:
                    title_row.controls.append(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, tooltip='AID needs to be caught up.'))
                    # self.kel_update_dialog = KELUpdateConfirmDialog(self.app, self.app.page, hab, aid_update)
                    title_row.controls.append(
                        ft.OutlinedButton(text='Update Log', data=(hab, aid_update), on_click=self.kel_update)
                    )
                tile = ft.ListTile(
                    leading=ft.Icon(
                        icon,
                        tooltip=tip,
                    ),
                    title=ft.Text(
                        value=hab.name,
                        color=colouring.Colouring.get(colouring.Colouring.ON_SURFACE),
                    ),
                    subtitle=title_row,
                    trailing=ft.PopupMenuButton(
                        tooltip=None,
                        icon=ft.Icons.MORE_VERT,
                        items=[
                            view,
                            rotate,
                            delete,
                        ],
                    ),
                    on_click=self.view_identifier,
                    data=hab,
                    shape=ft.StadiumBorder(),
                )
                self.list.controls.append(
                    ft.Container(
                        content=tile,
                    )
                )
                self.list.controls.append(ft.Divider(opacity=0.1))

        self.update()

    async def view_identifier(self, e):
        """
        View the identifier details.

        Args:
            e: The event object containing the identifier data.

        Returns:
            None
        """
        hab = e.control.data
        self.app.page.route = f'/identifiers/{hab.pre}/view'
        self.app.page.update()

    async def rotate_identifier(self, e):
        """
        Rotates the identifier associated with the given event.

        Args:
            e (Event): The event containing the identifier to rotate.

        Returns:
            None
        """
        hab = e.control.data
        self.app.page.route = f'/identifiers/{hab.pre}/rotate'
        self.app.page.update()

    async def delete_identifier(self, e):
        """
        Deletes an identifier from the application.

        Args:
            e: The event object containing the identifier information.

        Returns:
            None
        """
        hab = e.control.data
        self.app.hby.deleteHab(hab.name)

        self.card.content.update()  # type: ignore

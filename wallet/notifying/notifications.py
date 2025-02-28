import logging
from datetime import datetime

import flet as ft

from wallet.notifying.group_inception_request import NoticeMultisigGroupInception
from wallet.notifying.group_rotation_request import NoticeMultisigGroupRotation
from wallet.notifying.notification import NotificationsBase

logger = logging.getLogger('wallet')


class Notifications(NotificationsBase):
    """
    Represents a notifications component in the application.

    Args:
        app(apping.CitadelApp): The application instance.

    Attributes:
        list(ft.Column): A column control that holds the list of notifications.
        app(apping.CitadelApp): The application instance.
        notes(list): A list to store the notifications.

    Methods:
        route_note: Routes to a specific notification.
        delete_note: Deletes a notification.
        dismiss: Dismisses the current notification.
        build: Builds the notifications component.
        note_view: Displays the view for a specific notification.
    """

    def __init__(self, app):
        self.list = ft.Column([], spacing=0, expand=True)
        self.app = app
        self.notes = []

        super().__init__(app, self.list, ft.Text('Notifications', size=24))

    async def route_note(self, e):
        """
        Routes a note and updates the page.

        Args:
            e (Event): The event containing the note data.

        Returns:
            None
        """
        note = e.control.data
        self.app.agent.notifier.mar(note.rid)
        self.app.agent.noter.update()

        self.app.page.route = f'/notifications/{note.rid}'
        self.app.page.update()

    async def delete_note(self, e):
        """
        Deletes a note.

        Args:
            e: The note to be deleted.

        Returns:
            None
        """
        self.app.agent.notifier.rem(rid=e.control.data.rid)
        self.app.page.update()

    async def dismiss(self, _):
        """
        Dismisses the notification and updates the page route.

        Args:
            _: Placeholder argument (ignored).

        Returns:
            None
        """
        self.app.page.route = '/notifications'
        self.app.page.update()

    def did_mount(self):
        """
        Builds the notifications and returns the user interface elements.

        This method retrieves the notifications from the notifier agent and creates
        user interface elements (tiles) for each notification. The tiles are then
        appended to a list control. Finally, a column layout is created with a row
        containing an icon and a title, and another row containing a card.

        Returns:
            A `Column` object representing the user interface elements for the notifications.
        """
        self.list.controls.clear()
        self.notes = self.app.agent.notifier.getNotes(start=0, end=self.app.agent.notifier.getNoteCnt())

        if len(self.notes) == 0:
            self.controls = [
                ft.Column(
                    [
                        self.title,
                        ft.Row([ft.Text('Such empty...')]),
                    ]
                )
            ]

        self.notes.sort(key=lambda note: datetime.fromisoformat(note.datetime), reverse=True)

        for note in self.notes:
            attrs = note.attrs
            route = attrs['r']
            dt = datetime.fromisoformat(note.datetime)
            dt_fmt = dt.strftime('%Y-%m-%d %I:%M %p')

            match route:
                case '/multisig/icp':
                    tile = ft.ListTile(
                        leading=ft.Icon(ft.Icons.PEOPLE_ROUNDED),
                        title=ft.Text('Group Inception Request'),
                        subtitle=ft.Text(f'{dt_fmt}'),
                        trailing=ft.PopupMenuButton(
                            tooltip=None,
                            icon=ft.Icons.MORE_VERT,
                            items=[
                                ft.PopupMenuItem(
                                    text='View',
                                    icon=ft.Icons.PAGEVIEW,
                                    data=note,
                                    on_click=self.route_note,
                                ),
                                ft.PopupMenuItem(
                                    text='Delete', icon=ft.Icons.DELETE_FOREVER, on_click=self.delete_note, data=note
                                ),
                            ],
                        ),
                        data=note,
                        on_click=self.route_note,
                        shape=ft.StadiumBorder(),
                    )
                    self.list.controls.append(tile)
                case '/multisig/rot':
                    tile = ft.ListTile(
                        leading=ft.Icon(ft.Icons.PEOPLE_ROUNDED),
                        title=ft.Text('Group Rotation Request'),
                        subtitle=ft.Text(f'{dt_fmt}'),
                        trailing=ft.PopupMenuButton(
                            tooltip=None,
                            icon=ft.Icons.MORE_VERT,
                            items=[
                                ft.PopupMenuItem(
                                    text='View',
                                    icon=ft.Icons.PAGEVIEW,
                                    data=note,
                                    on_click=self.route_note,
                                ),
                                ft.PopupMenuItem(
                                    text='Delete', icon=ft.Icons.DELETE_FOREVER, on_click=self.delete_note, data=note
                                ),
                            ],
                        ),
                        data=note,
                        on_click=self.route_note,
                        shape=ft.StadiumBorder(),
                    )
                    self.list.controls.append(tile)
            self.list.controls.append(ft.Divider(opacity=0.1))

        self.controls = [
            ft.Column(
                [
                    self.title,
                    ft.Row([self.card]),
                ]
            )
        ]

    def note_view(self, note_id):
        """
        Retrieves a note by its ID and returns a corresponding notice object based on the note's route.

        Args:
            note_id (str): The ID of the note to retrieve.

        Returns:
            Notice: A notice object based on the note's route.

        Raises:
            KeyError: If the note's route is not recognized.

        """
        note, _ = self.app.agent.notifier.noter.get(note_id)
        attrs = note.attrs
        route = attrs['r']

        match route:
            case '/multisig/icp':
                return NoticeMultisigGroupInception(self.app, note)
            case '/multisig/rot':
                return NoticeMultisigGroupRotation(self.app, note)

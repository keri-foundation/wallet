import logging
from typing import List

import flet as ft

logger = logging.getLogger('wallet')


class ContactBase(ft.Column):
    """
    Base class for contacts in the application.

    Args:
        app: The application object.
        panel: The panel object.
        title (ft.Row): The title panel.

    Attributes:
        app: The application object.
        panel: The panel object.
        card: The container for the panel.
    """

    def __init__(self, app, panel, title=None):
        self.app = app
        title = title if title else ft.Row()
        self.panel = panel
        self.card = ft.Container(content=self.panel, expand=True, alignment=ft.alignment.top_left)

        super().__init__(
            [
                title,
                ft.Row([self.card]),
            ],
            expand=True,
            scroll=ft.ScrollMode.ALWAYS,
        )


def filter_witnesses(contacts: List[dict]) -> List[dict]:
    """Return contacts that are not witnesses."""
    controllers = []
    for contact in contacts:
        if 'tag=witness' not in contact['oobi']:
            if 'type' in contact and 'witness' in contact['type']:
                continue
            controllers.append(contact)
    return controllers

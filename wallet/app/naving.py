import flet as ft


class Navbar(ft.Stack):
    IDENTIFIERS = 0
    CONTACTS = 1
    WITNESSES = 2
    SETTINGS = 3

    def __init__(self, page: ft.Page):
        super().__init__()
        self.page = page

        destinations = [
            ft.NavigationRailDestination(
                icon=ft.Icons.DATASET_LINKED,
                selected_icon=ft.Icons.DATASET_LINKED_OUTLINED,
                label='Identifiers',
                padding=ft.padding.all(10),
            ),
            ft.NavigationRailDestination(
                icon_content=ft.Icon(ft.Icons.PEOPLE),
                selected_icon_content=ft.Icon(ft.Icons.PEOPLE_OUTLINE),
                label='Contacts',
                padding=ft.padding.all(10),
            ),
            ft.NavigationRailDestination(
                icon_content=ft.Icon(ft.Icons.VIEW_COMFY_ALT),
                selected_icon_content=ft.Icon(ft.Icons.VIEW_COMFY_ALT_OUTLINED),
                label='Witnesses',
                padding=ft.padding.all(10),
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon_content=ft.Icon(ft.Icons.SETTINGS),
                label_content=ft.Text('Settings'),
                padding=ft.padding.all(10),
            ),
        ]

        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            min_extended_width=400,
            destinations=destinations,
            on_change=self.nav_change,
            expand=True,
        )

    def did_mount(self):
        self.controls = [self.rail]

    async def nav_change(self, e):
        index = e if (type(e) is int) else e.control.selected_index
        self.rail.selected_index = index
        if index == self.IDENTIFIERS:
            self.page.route = '/identifiers'
        elif index == self.CONTACTS:
            self.page.route = '/contacts'
        elif index == self.WITNESSES:
            self.page.route = '/witnesses'
        elif index == self.SETTINGS:
            self.page.route = '/settings'

        self.page.update()

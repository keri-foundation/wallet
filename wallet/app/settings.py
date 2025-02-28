import flet as ft
from flet.core import padding
from keri.core import coring
from keri.core.coring import Tiers

from wallet.app.colouring import Colouring
from wallet.storing import THEME_KEY
from wallet.version import __version__


class Settings(ft.Column):
    def __init__(self, app):
        self.app = app
        self.settings = SettingsPanel(self.app)
        self.card = ft.Container(
            content=self.settings, padding=padding.only(left=10, top=15), expand=True, alignment=ft.alignment.top_left
        )

        super(Settings, self).__init__([ft.Row([self.card])], expand=True, scroll=ft.ScrollMode.ALWAYS)


class SettingsPanel(ft.Stack):
    def __init__(self, app):
        self.app = app
        super(SettingsPanel, self).__init__()

        async def theme_changed(e):
            await self.app.page.client_storage.set_async(THEME_KEY, e.data)
            self.page.theme_mode = e.data
            self.app.colouring = Colouring.set_theme(self.app.page.theme_mode)
            self.app.page.update()
            self.app.navbar.update()
            self.page.appbar.update()

        def temp_changed(e):
            self.app.temp = self.tempSwitch.value

        self.tempSwitch = ft.Switch(
            value=self.app.temp, on_change=temp_changed, label_position=ft.LabelPosition.LEFT, thumb_color=ft.Colors.RED_400
        )

        def base_changed(e):
            self.app.base = self.baseDir.value

        self.baseDir = ft.TextField(
            label='Directory', width=200, value=self.app.base, border_color=ft.Colors.RED_400, on_change=base_changed
        )

        def tier_changed(e):
            self.app.tier = self.tierGroup.value

        self.tierGroup = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(value=Tiers.low, label='Low', fill_color=ft.Colors.RED_400),
                    ft.Radio(value=Tiers.med, label='Medium', fill_color=ft.Colors.RED_400),
                    ft.Radio(value=Tiers.high, label='High', fill_color=ft.Colors.RED_400),
                ]
            ),
            on_change=tier_changed,
            value=self.app.tier,
        )

        self.theme_group = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(
                        value=ft.ThemeMode.SYSTEM.name,
                        label='Auto',
                    ),
                    ft.Radio(
                        value=ft.ThemeMode.LIGHT.name,
                        label='Light',
                    ),
                    ft.Radio(
                        value=ft.ThemeMode.DARK.name,
                        label='Dark',
                    ),
                ]
            ),
            value=self.app.page.theme_mode,
            on_change=theme_changed,
        )

        def algo_changed(e):
            self.app.algo = self.algoGroup.value
            self.keyTypePanel.content = self.salty if self.app.algo == 'salty' else None
            self.keyTypePanel.update()

        self.algoGroup = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Radio(value='salty', label='Salty', fill_color=ft.Colors.RED_400),
                    ft.Radio(value='randy', label='Randy', fill_color=ft.Colors.RED_400),
                ]
            ),
            on_change=algo_changed,
            value=self.app.algo,
        )

        def salt_changed(e):
            self.app.salt = self.salt.value

        self.salt = ft.TextField(
            label='Key Salt',
            value=self.app.salt,
            password=True,
            can_reveal_password=True,
            width=300,
            border_color=ft.Colors.RED_400,
            on_change=salt_changed,
        )

        async def resalt(_):
            self.app.salt = coring.randomNonce()[2:23]
            self.salt.value = self.app.salt
            self.salt.update()

        self.salty = ft.Column(
            [
                ft.Row(
                    [
                        self.salt,
                        ft.IconButton(
                            icon=ft.Icons.CHANGE_CIRCLE_OUTLINED,
                            on_click=resalt,
                        ),
                    ]
                )
            ],
            spacing=20,
        )
        self.keyTypePanel = ft.Container(content=self.salty)

    def did_mount(self):
        self.controls = [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    'Theme',
                                    weight=ft.FontWeight.BOLD,
                                ),
                                self.theme_group,
                            ],
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    'Danger Zone',
                                    color=Colouring.get(Colouring.RED),
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Container(
                                    content=ft.Column(
                                        [
                                            ft.Column(
                                                [
                                                    ft.Row(
                                                        [ft.Text('Temporary Datastore', width=200), self.tempSwitch],
                                                        spacing=20,
                                                    ),
                                                    ft.Row(
                                                        [ft.Text('Database Directory Base', width=200), self.baseDir],
                                                        spacing=20,
                                                    ),
                                                    ft.Row(
                                                        [ft.Text('Cryptographic Key Strength', width=200), self.tierGroup],
                                                        spacing=20,
                                                    ),
                                                    ft.Row(
                                                        [ft.Text('Default Key Generation', width=200), self.algoGroup],
                                                        spacing=20,
                                                    ),
                                                    ft.Row([ft.Text('', width=200), self.keyTypePanel]),
                                                ]
                                            )
                                        ]
                                    ),
                                    padding=ft.padding.all(25),
                                    border=ft.border.all(0.35, ft.Colors.RED_400),
                                ),
                            ]
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    f'Version: {__version__}',
                                ),
                            ]
                        ),
                    ],
                    spacing=35,
                    scroll=ft.ScrollMode.AUTO,
                ),
                expand=True,
                alignment=ft.alignment.top_left,
                padding=padding.only(left=10, top=15, right=20),
            )
        ]

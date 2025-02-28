import asyncio
import logging

import flet as ft
from keri import kering
from keri.core import serdering

from wallet.app.colouring import Colouring
from wallet.notifying.notification import NotificationsBase

logger = logging.getLogger('wallet')


class NoticeMultisigGroupInception(NotificationsBase):
    """
    Represents a notification for a multisig group inception request.

    Args:
        app (App): The application instance.
        note (Note): The notification object.

    Attributes:
        app (App): The application instance.
        note (Note): The notification object.
        said (str): The notification attribute 'd'.
        mhab (Optional[Habitant]): The member's habitant object.
        ked (Optional[dict]): The cloned KERI event data.
        embeds (Optional[dict]): The embedded data.
        btn_join (ElevatedButton): The 'Join' button.
        group_info_pacifier (Row): The row displaying the fetching notification information message.
        members_list (Column): The column displaying the list of members.
        group_id (TextField): The text field displaying the group ID.
        group_alias (TextField): The text field for entering the group alias.
        group_info (Column): The column containing the group information.
    """

    def __init__(self, app, note):
        self.app = app
        self.note = note
        self.said = note.attrs['d']
        self.mhab = None
        self.ked = None
        self.embeds = None
        self.signing_members = []
        self.rotation_members = []

        self.btn_join = ft.ElevatedButton(
            'Join',
            on_click=self.join,
            data=note.rid,
            disabled=True,
        )

        self.group_info_pacifier = ft.Row([ft.Text('Fetching notification information...')], visible=True)

        self.members_list = ft.Column([])
        self.group_id = ft.TextField(
            label='Group ID',
            value='',
            read_only=True,
            text_style=ft.TextStyle(font_family='monospace'),
        )

        self.group_alias = ft.TextField(label='Enter alias', value='')

        self.current_threshold = ft.TextField(
            label='Number of current signers required:',
            value='',
            read_only=True,
            visible=False,
        )
        self.next_threshold = ft.TextField(
            label='Number of next signers required:',
            value='',
            read_only=True,
            visible=False,
        )

        self.group_info = ft.Column(
            controls=[
                self.group_id,
                self.group_alias,
                ft.Divider(),
                ft.Row([ft.Text('Proposed members:')]),
                self.members_list,
                ft.Divider(),
                self.current_threshold,
                self.next_threshold,
                ft.Divider(),
            ],
            visible=False,
            width=500,
        )

        super().__init__(
            app,
            self.panel(),
            ft.Row(
                controls=[
                    ft.Container(
                        ft.Text(value='Group Inception Request', size=24),
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

    async def cancel(self, e):
        self.app.page.route = '/notifications'
        self.app.page.update()

    def did_mount(self):
        self.page.run_task(self.get_exchange_message)

    async def get_exchange_message(self):
        """
        Retrieves the exchange message from the agent's cloner and updates the UI accordingly.

        Returns:
            None
        """
        self.app.agent.cloner.clone(self.said)

        while self.said not in self.app.agent.cloner.cloned:
            await asyncio.sleep(1)

        cloned = self.app.agent.cloner.cloned[self.said]

        self.ked = cloned.ked
        self.embeds = self.ked['e']

        if isinstance(self.embeds['icp']['kt'], str):
            self.current_threshold.value = self.embeds['icp']['kt']
            self.current_threshold.visible = True
        elif isinstance(self.embeds['icp']['kt'], list):
            logger.info('Current threshold is a list')
        else:
            logger.error('Thresholds are not of type str or list')

        if isinstance(self.embeds['icp']['nt'], str):
            self.next_threshold.value = self.embeds['icp']['nt']
            self.next_threshold.visible = True
        elif isinstance(self.embeds['icp']['nt'], list):
            logger.info('Next threshold is a list')
        else:
            logger.error('Thresholds are not of type str or list')

        self.signing_members = self.ked['a']['smids']
        self.rotation_members = self.ked['a']['rmids']

        org = self.app.agent.org
        for member in self.signing_members:
            if org.get(member):
                alias = org.get(member)['alias']
            else:
                self.mhab = self.app.hby.habByPre(member)
                if self.mhab:
                    alias = f'You ({self.mhab.name})'
                else:
                    alias = 'Unknown'

            self.members_list.controls.append(
                ft.TextField(
                    label=alias,
                    value=member,
                    read_only=True,
                    text_style=ft.TextStyle(font_family='monospace'),
                )
            )
        gid = self.ked['a']['gid']
        self.group_id.value = gid
        self.group_alias.value = self.app.hby.habs[gid].name
        self.group_info_pacifier.visible = False
        self.group_info.visible = True
        self.btn_join.disabled = False

        self.update()

        return None

    def panel(self):
        """
        Creates and returns a panel containing the group inception request information.

        Returns:
            ft.Container: The panel containing the group inception request information.
        """
        return ft.Container(
            ft.Column(
                [
                    self.group_info_pacifier,
                    self.group_info,
                    ft.Row(
                        [
                            self.btn_join,
                            ft.ElevatedButton('Dismiss', on_click=self.dismiss),
                        ]
                    ),
                    ft.Container(padding=ft.padding.only(bottom=80)),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=True,
            padding=ft.padding.only(left=10, top=15, bottom=100),
        )

    async def join(self, e):
        """
        Join a group.

        This method is called when a user wants to join a multisig group. It performs the following steps:
        1. Checks if a group alias is provided. If not, displays an error message and returns.
        2. Extracts necessary information from the embedded inception message.
        3. Constructs a group Habitat using the extracted information.
        4. Appends the ICP message to the agent's list of groups.

        Parameters:
        - _: Placeholder parameter, not used in the method.

        Returns:
        - None

        Raises:
        - None
        """
        rid = e.control.data
        if self.group_alias.value == '':
            self.group_alias.border_color = Colouring.get(Colouring.RED)
            self.app.snack('Enter an alias for the group')
            self.update()
            return

        inits = {}

        oicp = serdering.SerderKERI(sad=self.embeds['icp'])

        inits['isith'] = oicp.ked['kt']
        inits['nsith'] = oicp.ked['nt']

        inits['estOnly'] = kering.TraitCodex.EstOnly in oicp.ked['c']
        inits['DnD'] = kering.TraitCodex.DoNotDelegate in oicp.ked['c']

        inits['toad'] = oicp.ked['bt']
        inits['wits'] = oicp.ked['b']
        inits['delpre'] = oicp.ked['di'] if 'di' in self.ked else None

        ghab = self.app.hby.makeGroupHab(
            group=self.group_alias.value,
            mhab=self.mhab,
            smids=self.signing_members,
            rmids=self.rotation_members,
            **inits,
        )

        self.app.agent.groups.append(dict(serder=oicp))
        self.app.agent.joining[ghab.pre] = rid

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

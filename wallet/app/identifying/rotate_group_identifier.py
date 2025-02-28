"""
rotate_group_identifier.py - Panel for rotating a group multisig identifier.
"""

import logging
from typing import List, Set

import flet as ft
from flet.core.types import FontWeight
from keri import kering
from keri.app import connecting
from keri.app.habbing import GroupHab, Hab, Habery
from keri.core import coring, serdering
from keri.core.eventing import Kever

from wallet.app.colouring import Colouring
from wallet.app.contacting.contact import filter_witnesses
from wallet.app.identifying.identifier import IdentifierBase
from wallet.app.oobing.oobi_resolver_service import OOBIResolverService
from wallet.core import grouping
from wallet.core.grouping import GroupMember, create_participant_fn, create_rotation_event, filter_my_hab
from wallet.logs import log_errors

logger = logging.getLogger('wallet')


class RotateGroupIdentifierPanel(IdentifierBase):
    """Panel for rotating a group multisig identifier."""

    def __init__(self, app, hab):
        """
        Creates a panel for rotating a group multisig identifier.

        Set up signing and rotation members and their thresholds for selecting which members will be a part of the
        upcoming multisig group rotation event.
        """
        self.app = app
        self.hby: Habery = app.agent.hby
        self.org = connecting.Organizer(hby=self.hby)
        self.group_hab: GroupHab = hab
        kever: Kever = self.group_hab.kever

        # Find local hab in the group
        self.hab: Hab = filter_my_hab(self.hby.habs, self.group_hab.smids)
        self.name: str = self.hab.name  # Name (alias) of current agent AID
        self.pre: str = self.hab.pre  # Prefix of current agent AID

        self.participants: dict[str, GroupMember] = self._set_up_participants(kever)

        # Set up list of prior members
        self.contacts: List[dict] = filter_witnesses(self.org.list())

        # On read should be converted to hex unless fractionally weighted
        self.isith_field: ft.TextField = ft.TextField(value='0')
        self.nsith_field: ft.TextField = ft.TextField(value='0')

        # TOAD should always be converted from int to hex on read as BaseHab.rotate expects hex
        self.toad_field: ft.TextField = ft.TextField(
            value=kever.toader.num,
        )

        # Next participants = []
        self.next_participants: dict[str, GroupMember] = dict()
        self.use_prior_thresholds: bool = False

        # SMIDs Signing Member IDs (other participants with signing authority)
        self.smids: Set[str] = set()

        # RMIDs Rotation Member IDs (other participants with rotation authority)
        self.rmids: Set[str] = set()

        self.current_member_rows: List[ft.DataRow] = [
            self.member_row(p.alias, p.pre, p.sthold, p.rthold) for _, p in self.participants.items()
        ]

        # Next members dropdown
        self.next_member_options: List[ft.dropdown.Option] = []
        self.next_member_options.append(self.next_member_option(self.name, self.pre))  # add self as option
        self.next_member_options.extend([self.next_member_option(c['alias'], c['id']) for _, c in enumerate(self.contacts)])
        # Sort on alias
        self.next_member_options = sorted(
            self.next_member_options,
            key=lambda opt: opt.data[0],
        )
        self.next_dropdown = ft.Dropdown(
            options=self.next_member_options,
            width=535,
            text_size=14,
            text_style=ft.TextStyle(font_family='monospace'),
        )
        self.next_members_title = ft.Column(
            width=650,
            controls=[
                ft.Container(content=ft.Text('Signing Members', weight=FontWeight.BOLD), padding=ft.padding.only(0, 20, 0, 0))
            ],
        )
        self.next_member_list: List[dict] = []
        self.next_participant_rows: List[ft.DataRow] = []
        self.next_participant_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text('Alias', weight=FontWeight.BOLD)),
                ft.DataColumn(ft.Text('Prefix', weight=FontWeight.BOLD)),
                ft.DataColumn(ft.Text('Sign', weight=FontWeight.BOLD)),
                ft.DataColumn(ft.Text('', weight=FontWeight.BOLD)),  # threshold col
                ft.DataColumn(ft.Text('Rotate', weight=FontWeight.BOLD)),
                ft.DataColumn(ft.Text('', weight=FontWeight.BOLD)),  # threshold col
                ft.DataColumn(ft.Text('Edit', weight=FontWeight.BOLD)),
                ft.DataColumn(ft.Text('', weight=FontWeight.BOLD)),  # delete col
            ],
            rows=self.next_participant_rows,
            width=750,
            column_spacing=10,
        )

        self.rotate_button = ft.ElevatedButton(
            'Rotate',
            on_click=self.on_rotate,
            disabled=True,
        )
        self.rotate_progress_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

        super(RotateGroupIdentifierPanel, self).__init__(
            app,
            self.panel(),
            self._build_title(),
        )

    def _build_title(self) -> ft.Row:
        """Constructs title row for base panel layout at top of panel"""
        return ft.Row(
            controls=[
                ft.Container(
                    ft.Text(value=f'Alias: {self.hab.name}', size=24),
                    padding=ft.padding.only(10, 0, 10, 0),
                ),
                ft.Container(
                    ft.IconButton(icon=ft.Icons.CLOSE, on_click=self.back_to_identifier),
                    alignment=ft.alignment.top_right,
                    expand=True,
                    padding=ft.padding.only(0, 0, 10, 0),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _set_up_participants(self, kever):
        """Reads smids and rmids to create a GroupMember list of existing signers and rotators."""
        smids = self.group_hab.smids
        rmids = self.group_hab.rmids
        if rmids is None:
            rmids = smids
        pre = self.pre
        name = self.name
        contacts: List[dict] = filter_witnesses(self.org.list())

        # Signing thresholds of prior group members
        sign_tholds = coring.Tholder(sith=kever.serder.ked['kt'])
        smid_tholds: dict = {pre: thold for pre, thold in zip(smids, sign_tholds.sith)}
        self.smid_tholds = smid_tholds

        # Rotation thresholds of prior next group members
        # can be different from signing members so should be handled separately
        rot_tholds = coring.Tholder(sith=kever.serder.ked['nt'])
        rmid_tholds = {pre: thold for pre, thold in zip(rmids, rot_tholds.sith)}
        self.rmid_tholds = rmid_tholds
        create_participant = create_participant_fn(smids, rmids, smid_tholds, rmid_tholds)

        participants = dict()
        # Add local participant
        participants[pre] = create_participant(name, pre)
        for contact in contacts:
            if contact['id'] in smids:
                participants[contact['id']] = create_participant(contact['alias'], contact['id'])
            elif contact['id'] in rmids:
                participants[contact['id']] = create_participant(contact['alias'], contact['id'])
        participants = {k: v for k, v in sorted(participants.items(), key=lambda p: p[1].alias)}
        return participants

    def _build_next_participant_rows(self):
        """Builds rows for the next member table"""
        self.next_participant_rows.clear()
        for _, p in self.next_participants.items():
            self.next_participant_rows.append(self.next_participant_row(p.alias, p.pre, p.sthold, p.rthold))

    @staticmethod
    def next_member_option(alias: str, pre: str) -> ft.dropdown.Option:
        return ft.dropdown.Option(key=pre, text=f'{alias} - {pre}', data=(alias, pre))

    def append_option_once(self, m_alias: str, m_pre: str):
        """
        m_alias: str - New member alias
        m_pre: str - New member prefix
        """
        for opt in self.next_member_options:
            (alias, pre) = opt.data
            if m_pre == pre:
                return
        logger.debug(f'Adding {m_alias} to dropdown')
        self.next_member_options.append(self.next_member_option(m_alias, m_pre))
        self.next_dropdown.update()

    async def query_key_state(self, alias, pre):
        contact = self.org.get(pre)
        if pre in self.hby.habs.keys():
            return  # Do not need to query own key state.
        await OOBIResolverService(self.app).resolve_oobi(pre=pre, oobi=contact['oobi'], alias=contact['alias'])
        self.app.snack(f"Resolved {alias}'s key state for AID {pre}...")

    async def show_progress_ring(self):
        self.rotate_progress_ring.visible = True
        self.page.update()

    async def hide_progress_ring(self):
        self.rotate_progress_ring.visible = False
        self.page.update()

    def get_sthold(self, pre, signer_count):
        if self.use_prior_thresholds:
            if pre in self.smid_tholds:
                return self.smid_tholds[pre]
            else:
                return grouping.calc_weights(signer_count)
        else:
            return grouping.calc_weights(signer_count)

    def get_rthold(self, pre, rotator_count):
        if self.use_prior_thresholds:
            if pre in self.rmid_tholds:
                return self.rmid_tholds[pre]
            else:
                return grouping.calc_weights(rotator_count)
        else:
            return grouping.calc_weights(rotator_count)

    async def add_participant(self, pre):
        # Remove from dropdown
        opt = next(filter(lambda opt: opt.data[1] == pre, self.next_member_options))
        alias, _ = opt.data
        logger.debug(f'Removing {alias} from dropdown')
        self.next_member_options.remove(opt)
        self.next_dropdown.value = None
        self.next_dropdown.update()

        # Add group member to next_participants with existing thresholds +1
        signer_count = len(list(filter(lambda p: p.sthold is not None, self.next_participants.values())))
        rotator_count = len(list(filter(lambda p: p.rthold is not None, self.next_participants.values())))
        sthold = self.get_sthold(pre, signer_count + 1)
        rthold = self.get_rthold(pre, rotator_count + 1)
        self.next_participants[pre] = GroupMember(alias=alias, pre=pre, sthold=sthold, rthold=rthold)
        self.rebalance_next_participant_thresholds()
        self.update_next_participants()

        self.page.update()
        logger.info(f'Added member {alias} to multisig group...')
        return (alias, pre)

    @log_errors
    async def add_handler(self, _):
        """
        Adds a participant to both the current signers and rotators.
        """
        if self.next_dropdown.value is None:
            self.app.snack('Must select a member in order to add.')
            return

        pre = self.next_dropdown.value

        await self.add_participant(pre)

    def inc_signer_count(self):
        """increments the signer count by one. used when adding the threshold of a participant"""
        signer_count = len(list(filter(lambda p: p.sthold is not None, self.next_participants.values())))
        return signer_count + 1

    def inc_rotator_count(self):
        """increments the rotator count by one. used when adding the threshold of a participant"""
        rotator_count = len(list(filter(lambda p: p.rthold is not None, self.next_participants.values())))
        return rotator_count + 1

    def rebalance_next_participant_thresholds(self):
        """
        Creates equal fractional weights depending on the count of signing or rotation members.
        Only counts members towards the total whose threshold is not None.
        """
        signer_count = len(list(filter(lambda p: p.sthold is not None, self.next_participants.values())))
        rotator_count = len(list(filter(lambda p: p.rthold is not None, self.next_participants.values())))
        for p in self.next_participants.values():
            sthold = self.get_sthold(p.pre, signer_count)
            rthold = self.get_rthold(p.pre, rotator_count)
            if p.sthold is not None:
                p.sthold = sthold
            if p.rthold is not None:
                p.rthold = rthold

        # Enable/disable rotate button based on thresholds
        if signer_count == 0 or rotator_count == 0:
            self.rotate_button.disabled = True
        else:
            self.rotate_button.disabled = False
        self.rotate_button.update()

    def update_next_participants(self):
        """The table rows must be recreated and added to the DataTable.rows property in order to be re-rendered."""
        self.next_participant_rows = [
            self.next_participant_row(p.alias, p.pre, p.sthold, p.rthold) for _, p in self.next_participants.items()
        ]
        self.next_participant_table.rows = self.next_participant_rows
        self.next_participant_table.update()

    @log_errors
    async def toggle_signing_participant(self, e):
        """Removes or adds a participant to the signers and updates the table"""
        pre = e.control.data
        self.next_participants[pre].sthold = (
            None if self.next_participants[pre].sthold is not None else self.inc_signer_count()
        )
        self.rebalance_next_participant_thresholds()
        self.update_next_participants()
        self.page.update()

    @log_errors
    async def toggle_rotation_participant(self, e):
        """Removes or adds a participant to the rotators and updates the table"""
        pre = e.control.data
        self.next_participants[pre].rthold = (
            None if self.next_participants[pre].rthold is not None else self.inc_rotator_count()
        )
        self.rebalance_next_participant_thresholds()
        self.update_next_participants()
        self.page.update()

    async def remove_participant(self, pre):
        """Remove a participant from the next participants list and update the thresholds and options."""
        if pre in self.next_participants:
            p = self.next_participants.pop(pre)
            print(f'Removing participant {pre}')
            self.rebalance_next_participant_thresholds()
            self.update_next_participants()

            self.append_option_once(p.alias, p.pre)

            self.page.update()

    async def refresh_next_participants(self):
        self.update_next_participants()
        self.page.update()

    @log_errors
    async def delete_handler(self, e):
        pre = e.control.data
        p = self.next_participants[pre]
        await self.remove_participant(p.pre)

    @log_errors
    async def toggle_prior_members(self, e):
        use_prior_members = e.control.value
        prior_member_prefixes = [pre for pre, m in self.participants.items()]
        if use_prior_members:
            logger.info(f'Adding members from prior group: {prior_member_prefixes}')
            for pre, m in self.participants.items():
                await self.add_participant(m.pre)
        else:
            for pre in prior_member_prefixes:
                await self.remove_participant(pre)

        self.page.update()

    @log_errors
    async def on_change_use_thresholds(self, e):
        self.use_prior_thresholds = e.control.value
        self.page.update()

    @staticmethod
    def parse_toad(toad: str) -> int:
        try:
            return int(toad)
        except ValueError:
            try:
                return int(toad, 16)
            except ValueError:
                raise ValueError(f'Invalid TOAD value not int or hex: {toad}')

    @log_errors
    async def back_to_identifier(self, e):
        self.app.page.route = f'/identifiers/{self.group_hab.pre}/view'
        self.app.page.update()

    @log_errors
    async def on_rotate(self, _):
        smids = [p.pre for p in self.next_participants.values() if p.sthold is not None]
        isith = [p.sthold for p in self.next_participants.values() if p.sthold is not None]
        rmids = [p.pre for p in self.next_participants.values() if p.rthold is not None]
        nsith = [p.rthold for p in self.next_participants.values() if p.rthold is not None]
        if len(rmids) == 0:
            self.app.snack('Error: No rotation members selected. Select rotation members to perform rotation')
            return
        try:
            hex_toad = f'{self.toad_field.value:0x}'  # BaseHab.rotate expects hex encoded TOAD
            rot = create_rotation_event(
                hby=self.hby,
                ghab=self.group_hab,
                smids=smids,
                rmids=rmids,
                wits=None,
                cuts=None,
                adds=None,
                isith=isith,
                nsith=nsith,
                toad=self.parse_toad(hex_toad),
                data=None,
            )
        except kering.ValidationError as ex:
            if 'invalid rotation' in ex.args[0]:
                self.app.snack(
                    'Error: Invalid rotation members or keystate. Rotate keys and refresh keystate for all members.', 10000
                )
                return
            else:
                raise ex
        logger.info(f'Rotating multisig identifier from {self.name}...')
        await self.show_progress_ring()

        self.app.agent.groups.push(dict(serder=serdering.SerderKERI(raw=rot), rot=rot, smids=smids, rmids=rmids))
        self.app.snack(f'Rotating multisig identifier{self.group_hab.pre}, waiting for multisig collaboration...')

    @log_errors
    async def on_cancel(self, _):
        self.app.page.route = '/identifiers'
        self.app.page.update()

    @log_errors
    async def edit_handler(self, e):
        pre = e.control.data
        dialog = ThresholdChangeDialog(self.app, pre, self.next_participants, self.refresh_next_participants)
        self.page.dialog = dialog
        await dialog.open_dialog()

    def member_row(self, alias: str, pre: str, sthold, rthold) -> ft.DataRow:
        return ft.DataRow(
            data=(alias, pre, sthold, rthold),
            cells=[
                ft.DataCell(ft.Text(f'{alias}')),
                ft.DataCell(ft.Text(f'{pre}', font_family='monospace')),
                ft.DataCell(ft.Text(f'{sthold if sthold else ""}')),
                ft.DataCell(ft.Text(f'{rthold if rthold else ""}')),
            ],
        )

    def next_participant_row(self, alias: str, pre: str, sthold, rthold) -> ft.DataRow:
        return ft.DataRow(
            data=(alias, pre, sthold, rthold),
            cells=[
                ft.DataCell(ft.Text(f'{alias}')),
                ft.DataCell(ft.Text(f'{pre}', font_family='monospace')),
                ft.DataCell(ft.Checkbox(value=True, data=pre, on_change=self.toggle_signing_participant)),
                ft.DataCell(ft.Text(f'{sthold if sthold else ""}')),
                ft.DataCell(ft.Checkbox(value=True, data=pre, on_change=self.toggle_rotation_participant)),
                ft.DataCell(ft.Text(f'{rthold if rthold else ""}')),
                ft.DataCell(ft.IconButton(icon=ft.Icons.MODE_EDIT_OUTLINE, data=pre, on_click=self.edit_handler)),
                ft.DataCell(
                    ft.IconButton(
                        icon=ft.Icons.DELETE, data=pre, on_click=self.delete_handler, icon_color=Colouring.get(Colouring.RED)
                    )
                ),
            ],
        )

    def panel(self):
        kever = self.group_hab.kever
        container = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Divider(color=Colouring.get(Colouring.SECONDARY)),
                    ),
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(
                                                'Alias:',
                                                weight=ft.FontWeight.BOLD,
                                                size=14,
                                            ),
                                            ft.Text(
                                                self.group_hab.name,
                                                size=14,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text('Prefix:', weight=ft.FontWeight.BOLD),
                                            ft.Text(
                                                self.group_hab.pre,
                                                font_family='monospace',
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                'Sequence Number:',
                                                weight=ft.FontWeight.BOLD,
                                                width=175,
                                            ),
                                            ft.Text(kever.sner.num),
                                        ]
                                    ),
                                ]
                            )
                        ]
                    ),
                    ft.Divider(),
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(
                                                'Current Group Members',
                                                weight=FontWeight.BOLD,
                                            )
                                        ]
                                    ),
                                    ft.DataTable(
                                        columns=[
                                            ft.DataColumn(ft.Text('Alias', weight=FontWeight.BOLD)),
                                            ft.DataColumn(ft.Text('Prefix', weight=FontWeight.BOLD)),
                                            ft.DataColumn(ft.Text('Sign', weight=FontWeight.BOLD)),
                                            ft.DataColumn(ft.Text('Rotate', weight=FontWeight.BOLD)),
                                        ],
                                        rows=[*self.current_member_rows],
                                    ),
                                ]
                            ),
                        ]
                    ),
                    ft.Row(
                        [
                            ft.Text(
                                'Multisig Next Signing Members (defaults to prior)',
                                weight=FontWeight.BOLD,
                            ),
                            ft.Checkbox(
                                label='Pre-fill prior group',
                                value=False,
                                on_change=self.toggle_prior_members,
                            ),
                            ft.Checkbox(
                                label='Use Prior Thresholds',
                                value=False,
                                on_change=self.on_change_use_thresholds,
                            ),
                        ]
                    ),
                    # Next participants table
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    self.next_dropdown,
                                    ft.IconButton(
                                        icon=ft.Icons.ADD,
                                        tooltip='Add Member',
                                        on_click=self.add_handler,
                                    ),
                                ]
                            ),
                            ft.Row([self.next_members_title]),
                            ft.Row([self.next_participant_table]),
                        ]
                    ),
                    ft.ExpansionTile(
                        title=ft.Text('Advanced Rotation Configuration'),
                        affinity=ft.TileAffinity.LEADING,
                        initially_expanded=False,
                        controls=[
                            ft.ListTile(title=ft.Text('Signing Weights')),
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Current signing weights',
                                                        weight=FontWeight.BOLD,
                                                        width=125,
                                                    ),
                                                    self.isith_field,
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'Current rotation weights',
                                                        weight=FontWeight.BOLD,
                                                        width=125,
                                                    ),
                                                    self.nsith_field,
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        'TOAD',
                                                        tooltip=ft.Tooltip(
                                                            message='Threshold of Accountable Duplicity.\nNumber of witness signatures required to make an event valid.',
                                                        ),
                                                        weight=FontWeight.BOLD,
                                                        width=55,
                                                    ),
                                                    self.toad_field,
                                                ]
                                            ),
                                        ]
                                    ),
                                ]
                            ),
                        ],
                    ),
                    ft.Column(
                        [
                            ft.Row([ft.Text('Actions', weight=FontWeight.BOLD)]),
                            ft.Row(
                                [
                                    self.rotate_button,
                                    ft.ElevatedButton(
                                        'Cancel',
                                        on_click=self.on_cancel,
                                    ),
                                    self.rotate_progress_ring,
                                ]
                            ),
                        ]
                    ),
                ],
                spacing=35,
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=True,
            alignment=ft.alignment.top_left,
            padding=ft.padding.only(left=5, top=5, bottom=140),
        )

        return container


class ThresholdChangeDialog(ft.AlertDialog):
    """
    Presents a dialog with a signing and rotation threshold edit area
    """

    def __init__(self, app, pre, next_participants, callback):
        self.app = app
        self.pre = pre
        self.next_participants = next_participants
        self.participant = next_participants[pre]
        self.width = 350
        self.signing_label = ft.Text(value='Signing Threshold', weight=FontWeight.BOLD)
        self.sith_num = ft.TextField(label='Num', width=75)
        self.sith_den = ft.TextField(label='Den', width=75)
        self.signing = ft.Row([self.sith_num, ft.Text(value='/'), self.sith_den])
        self.rotation_label = ft.Text(value='Rotation Threshold', weight=FontWeight.BOLD)
        self.rsith_num = ft.TextField(label='Num', width=75)
        self.rsith_den = ft.TextField(label='Den', width=75)
        self.rotation = ft.Row([self.rsith_num, ft.Text(value='/'), self.rsith_den])
        self.error_text = ft.Text(value='', visible=False, width=300)
        self.callback = callback

        super(ThresholdChangeDialog, self).__init__(
            modal=True,
            title=ft.Text('Edit Threshold'),
            content=ft.Column(
                controls=[
                    ft.Divider(),
                    self.signing_label,
                    self.signing,
                    self.rotation_label,
                    self.rotation,
                    ft.Divider(),
                    self.error_text,
                ],
                height=285,
            ),
            actions=[
                ft.OutlinedButton(text='Cancel', on_click=self.close_dialog),
                ft.ElevatedButton(text='Confirm', on_click=self.confirm_update),
            ],
        )
        self.can_timeout = False

    async def open_dialog(self):
        """
        Opens dialog
        """
        self.open = True
        self.app.page.update()

    async def close_dialog(self, _):
        """
        Closes dialog
        """
        self.open = False
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
            e (flet.Control): The button control triggering this update
        """
        try:
            sith_num = int(self.sith_num.value)
            sith_den = int(self.sith_den.value)
        except ValueError:
            await self.show_error('Invalid signing threshold. Must be integers')
            return
        try:
            rsith_num = int(self.rsith_num.value)
            rsith_den = int(self.rsith_den.value)
        except ValueError:
            await self.show_error('Invalid rotation threshold. Must be integers')
            return
        if sith_den == 0 or rsith_den == 0:
            await self.show_error('Denominator cannot be 0')
            return
        if (sith_num / sith_den) > 1 or (rsith_num / rsith_den) > 1:
            await self.show_error('Numerator cannot be greater than 1')
            return
        await self.hide_error()

        sith = f'{sith_num}/{sith_den}'
        rsith = f'{rsith_num}/{rsith_den}'
        logger.info(f'Confirming signing threshold of {sith} and {rsith} for {self.participant.alias}...')
        self.next_participants[self.pre].sthold = sith
        self.next_participants[self.pre].rthold = rsith
        await self.callback()
        await self.close_dialog(None)

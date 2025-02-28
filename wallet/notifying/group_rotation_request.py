import asyncio
import logging
import pprint
from datetime import datetime
from typing import List

import flet as ft
from flet.core.types import FontWeight
from keri.app import connecting, grouping
from keri.core import coring, serdering
from ordered_set import OrderedSet as oset

from wallet.app.colouring import Colouring
from wallet.app.contacting.contact import filter_witnesses
from wallet.app.identifying import Identifiers
from wallet.app.oobing.oobi_resolver import OobiResolver
from wallet.app.oobing.oobi_resolver_service import OOBIResolverService
from wallet.core.agenting import ExchangeCloner
from wallet.core.grouping import GroupMember, create_participant_fn, filter_my_hab
from wallet.logs import log_errors
from wallet.notifying.notification import NotificationsBase

logger = logging.getLogger('wallet')


class NoticeMultisigGroupRotation(NotificationsBase):
    """
    Represents a notification for a multisig group rotation request.

    Args:
        app (WalletApp): The application instance.
        note (Note): The notification object.

    Attributes:
        app (WalletApp): The application instance.
        hby (Habery): The agent's Habery - used to get the agent's Hab.
        org (Organizer): The agent's Organizer - used to get a contact list
        cloner (Cloner): The agent's event cloner - used to get the exchange message referred to by the notification
        note (Note): The notification object being responded to
        said (str): The notification attribute 'd'.
        mhab (Habitat): The member's single sig habitat (Hab) object.
        ked (dict): The cloned KERI event data - from the exn
        embeds (dict): The group rotation embedded data - includes rotation event.
        smids (list): The list of signing member identifiers.
        rmids (list): The list of rotation member identifiers.
        btn_join (ElevatedButton): The 'Join' button.
        join_progress_ring (ProgressRing): The progress ring displayed when joining the rotation.
        participants (dict): The set of signing and rotation members. Added in the did_mount initialization
        group_id (str): The AID of the multisig group being rotated.
        group_id_field (TextField): The text field displaying the group ID.
        group_alias (TextField): The text field for entering the group alias.
        group_rotation_row (Row): The row containing the group rotation table and group info
        content_col (Column): The column containing the group information.
    """

    def __init__(self, app, note):
        self.app = app
        self.hby = app.agent.hby
        self.org = self.app.agent.org
        self.cloner: ExchangeCloner = app.agent.cloner
        self.note = note
        self.said: str = note.attrs['d']  # exchange message identifier from notification

        self.hab = None
        self.mhab = None
        self.ked = None
        self.embeds = None
        self.smids = []
        self.rmids = []

        self.btn_join = ft.ElevatedButton(
            'Join',
            on_click=self.join,
            data=note.rid,
            disabled=True,
        )
        self.join_progress_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

        self.participants: dict[str, GroupMember] = dict()
        self.group_id = ''
        self.group_id_field = ft.TextField(
            label='Group ID',
            value=self.group_id,
            read_only=True,
            text_style=ft.TextStyle(font_family='monospace'),
        )

        self.group_alias = ft.TextField(label='Alias', value='', read_only=True)

        self.group_rotation_row = ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2)])
        date = datetime.fromisoformat(note.datetime).strftime('%Y-%m-%d %I:%M %p')
        self.content_col = ft.Column(
            [
                ft.Text(value=f'Notification Date: {date}', width=300, weight=ft.FontWeight.BOLD),
                self.group_rotation_row,
            ]
        )

        super().__init__(
            app,
            self.panel(),
            title=ft.Row(
                controls=[
                    ft.Container(
                        ft.Text(value='Group Rotation Request', size=24),
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

    def did_mount(self):
        self.page.run_task(self.init_component)

    async def cancel(self, e):
        self.app.page.route = '/notifications'
        self.app.page.update()

    def participant_row(self, alias: str, pre: str, sthold, rthold) -> ft.DataRow:
        """Renders a single participant as a DataRow"""
        return ft.DataRow(
            data=(alias, pre, sthold, rthold),
            cells=[
                ft.DataCell(ft.Text(f'{alias}' if self.hab.name != alias else f'{alias} (You)')),
                ft.DataCell(ft.Text(f'{pre}', font_family='monospace')),
                ft.DataCell(ft.Text(f'{sthold if sthold else ""}')),
                ft.DataCell(ft.Text(f'{rthold if rthold else ""}')),
            ],
        )

    def join_rotation_controls(self, participants) -> ft.Column:
        """Controls to show when joining the rotation"""
        participant_rows: List[ft.DataRow] = [
            self.participant_row(p.alias, p.pre, p.sthold, p.rthold) for _, p in participants.items()
        ]
        return ft.Column(
            [
                self.group_id_field,
                self.group_alias,
                ft.Row(
                    [
                        ft.Text('Refresh Key State:', width=175, weight=ft.FontWeight.BOLD),
                        ft.IconButton(
                            tooltip='Refresh key state',
                            icon=ft.Icons.REFRESH_ROUNDED,
                            on_click=self.refresh_keystate,
                            padding=ft.padding.only(right=10),
                        ),
                    ]
                ),
                ft.Divider(),
                ft.Row([ft.Text('Proposed members:')]),
                ft.DataTable(
                    columns=[
                        ft.DataColumn(ft.Text('Alias', weight=FontWeight.BOLD)),
                        ft.DataColumn(ft.Text('Prefix', weight=FontWeight.BOLD)),
                        ft.DataColumn(ft.Text('Sign', weight=FontWeight.BOLD)),
                        ft.DataColumn(ft.Text('Rotate', weight=FontWeight.BOLD)),
                    ],
                    rows=participant_rows,
                    column_spacing=10,
                    width=685,
                ),
                ft.Divider(),
                ft.Row(
                    [
                        self.btn_join,
                        ft.ElevatedButton('Dismiss', on_click=self.dismiss),
                        self.join_progress_ring,
                    ]
                ),
            ],
            width=685,
        )

    def group_resolve_controls(self):
        """Controls to show when resolving the Group multisig OOBI"""

        async def callback(result):
            await self.contact_resolved(result)

        return ft.Column(
            [
                ft.Row([ft.Text('Group not found in your contacts. Resolve the group multisig OOBI.')]),
                OobiResolver(self.app, callback).render(),
            ]
        )

    async def contact_resolved(self, result):
        """Callback function for when the multisig OOBI is resolved"""
        contact = self.org.get(self.group_id)
        self.group_alias.value = contact['alias']
        logger.info(f'Contact resolved with result {result}')
        logger.debug('Exn message is:\n%s', pprint.pformat(self.ked))
        if result:
            participants = await self.get_participants()
            self.group_rotation_row.controls.clear()
            self.group_id_field.value = self.group_id
            self.group_rotation_row.controls.append(self.join_rotation_controls(participants))
        else:
            self.group_rotation_row.controls.clear()
            self.group_rotation_row.controls.append(self.group_resolve_controls())
        self.page.update()

    async def query_key_state(self, name, pre):
        logger.info(f'Querying key state for {pre}')

    async def refresh_keystate(self, e):
        """
        Retrieving current key state for each member of the multisig Hab will push key state
        and other notifications through the system. This method can be removed once notifications
        are pushed through with a different mechanism.
        """
        logger.info(f'refreshing key state for local AID {self.mhab.name}')
        for pre in self.smids:
            if pre in self.app.agent.hby.habs.keys():
                continue  # don't query for self
            contact = self.org.get(pre)
            alias = contact['alias'] if 'alias' in contact else None
            logger.info(f'Querying key state for {contact["alias"]} with pre: {pre}')
            await OOBIResolverService(self.app).resolve_oobi(pre=contact['id'], oobi=contact['oobi'], alias=alias, force=True)
            self.app.snack(f"Resolved {contact['alias']}'s key state for AID {pre}")

    @staticmethod
    async def get_contacts(agent):
        """Gets the list of contacts from the organizer."""
        org = connecting.Organizer(agent.hby)
        return org.list()

    async def get_exchange_message(self):
        """Retrieves a message by SAID from the agent's cloner"""
        self.cloner.clone(self.said)
        while self.said not in self.cloner.cloned:
            await asyncio.sleep(0.25)
        return self.cloner.cloned[self.said]

    def get_local_group_hab(self, smids):
        """Based on signing members find the local group hab"""
        for smid in smids:
            if (mhab := self.app.hby.habByPre(smid)) is not None:
                self.mhab = mhab
                return mhab

    def build_participants(self, hby, smids, rmids, rot_evt):
        """Read participants from the group rotation event and then add the local participant"""
        sign_tholds = coring.Tholder(sith=rot_evt.ked['kt'])
        smid_tholds = {pre: thold for pre, thold in zip(smids, sign_tholds.sith)}
        rot_tholds = coring.Tholder(sith=rot_evt.ked['nt'])
        rmid_tholds = {pre: thold for pre, thold in zip(rmids, rot_tholds.sith)}
        create_participant = create_participant_fn(smids, rmids, smid_tholds, rmid_tholds)

        org = connecting.Organizer(hby=hby)
        contacts: List[dict] = filter_witnesses(org.list())
        participants = dict()

        prefixes = smids + rmids
        for prefix in prefixes:
            contact = next(filter(lambda c: c['id'] == prefix, contacts), None)
            alias = contact['alias'] if contact else 'Unknown'
            participants[prefix] = create_participant(alias, prefix)

        # add local participant
        hab = filter_my_hab(hby.habs, smids)
        participants[hab.pre] = create_participant(hab.name, hab.pre)
        self.hab = hab
        return participants

    async def get_participants(self):
        orot = serdering.SerderKERI(sad=self.embeds['rot'])
        participants = self.build_participants(self.hby, self.smids, self.rmids, orot)
        participants = {k: v for k, v in sorted(participants.items(), key=lambda p: p[1].alias)}
        return participants

    @log_errors
    async def init_component(self):
        """
        Check whether the group multisig ID is known (in the list of contacts).
        If not, then show the OOBI resolution component.
        """
        exn = await self.get_exchange_message()

        self.ked = exn.ked
        self.embeds = self.ked['e']
        gid = self.ked['a']['gid']
        self.group_id = gid
        self.group_id_field.value = self.group_id
        self.ghab = self.app.agent.hby.habByPre(self.group_id)

        self.smids = self.ked['a']['smids']
        self.rmids = self.ked['a']['rmids']
        self.mhab = self.get_local_group_hab(self.smids)
        participants = await self.get_participants()

        contact = self.org.get(self.group_id)
        aids = Identifiers.get_aids(self.app.agent)
        if not contact and self.group_id not in aids:
            self.group_rotation_row.controls.clear()
            self.group_rotation_row.controls.append(self.group_resolve_controls())
        else:
            self.group_alias.value = contact['alias'] if contact else self.app.hby.habs[gid].name
            self.group_rotation_row.controls.clear()
            self.group_rotation_row.controls.append(self.join_rotation_controls(participants))

        self.btn_join.disabled = False
        self.page.update()

    async def show_progress_ring(self):
        self.join_progress_ring.visible = True
        self.page.update()

    async def hide_progress_ring(self):
        self.join_progress_ring.visible = False
        if self.page and self.page.update:  # may have navigated away so self.page may be None
            self.page.update()

    @log_errors
    async def join(self, e):
        """
        Joins the group multisig rotation operation. Copied from `kli multisig join` rotate with minor edits.
        """
        if self.group_alias.value == '':
            self.group_alias.border_color = Colouring.get(Colouring.RED)
            self.app.snack('Enter an alias for the group')
            self.update()
            return

        rid = e.control.data
        logger.info(f'Joining group {rid}')

        group = self.group_alias.value
        smids = self.smids
        rmids = self.rmids
        embeds = self.ked['e']
        orot = serdering.SerderKERI(sad=embeds['rot'])

        both = list(set(smids + (rmids or [])))
        mhab = None
        for mid in both:
            if mid in self.app.hby.habs:
                mhab = self.app.hby.habs[mid]
                break

        if mhab is None:
            message = "Invalid multisig group inception request, aid list must contain a local identifier'"
            logger.error(message)
            self.app.snack(message, duration=5000)
            return False

        pre = orot.ked['i']
        if pre in self.app.hby.habs:
            ghab = self.app.hby.habs[pre]
        else:
            ghab = self.app.hby.joinGroupHab(pre, group=group, mhab=mhab, smids=smids, rmids=rmids)

        await self.show_progress_ring()
        try:
            ghab.rotate(serder=orot, smids=smids, rmids=rmids)
        except ValueError as e:
            logger.error(f'ValueError rotating group {group}: {e}')
            await self.hide_progress_ring()
            return False
        except Exception as e:
            logger.error(f'Exception rotating group {group}: {e}')
            await self.hide_progress_ring()
            return False

        rot = ghab.makeOwnEvent(allowPartiallySigned=True, sn=orot.sn)

        exn, ims = grouping.multisigRotateExn(ghab, smids=ghab.smids, rmids=ghab.rmids, rot=rot)
        others = list(oset(smids + (rmids or [])))

        others.remove(ghab.mhab.pre)

        for recpt in others:  # this goes to other participants only as a signaling mechanism
            self.app.agent.postman.send(
                src=ghab.mhab.pre,
                dest=recpt,
                topic='multisig',
                serder=exn,
                attachment=ims,
            )

            while not self.app.agent.postman.sent(said=exn.said):
                await asyncio.sleep(0.25)

            self.app.agent.postman.cues.clear()

        serder = serdering.SerderKERI(raw=rot)
        prefixer = coring.Prefixer(qb64=ghab.pre)
        seqner = coring.Seqner(sn=serder.sn)

        # TODO This should be blocking for the participating AID. You shouldn't be able to join
        #   another multisig operation with the same local AID until the prior one completes or is
        #   cancelled.
        self.app.agent.counselor.start(ghab, prefixer, seqner, coring.Saider(qb64=serder.said))
        while True:
            saider = self.app.hby.db.cgms.get(keys=(prefixer.qb64, seqner.qb64))
            if saider is not None:
                break
            await asyncio.sleep(0.25)
        await self.hide_progress_ring()

        logger.info(f'Group {group} rotation {serder.sn} joined')
        self.app.snack(f'Group rotation for {group} complete at event {serder.sn}.')
        self.app.page.route = f'/identifiers/{serder.pre}/view'

    async def dismiss(self, e):
        self.app.page.route = '/notifications'
        self.app.page.update()

    def panel(self):
        """The content returned for this notification control"""
        return ft.Container(
            self.content_col,
            expand=True,
            padding=ft.padding.only(left=10, top=15, bottom=130),
        )

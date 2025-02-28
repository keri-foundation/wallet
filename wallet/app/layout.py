import logging

import flet as ft
from keri.app import connecting, habbing

from wallet.app import contacting, identifying, settings, splashing
from wallet.app.contacting.create_contact import CreateContactPanel
from wallet.app.contacting.view_contact import ViewContactPanel
from wallet.app.identifying.create_identifier import CreateIdentifierPanel
from wallet.app.identifying.rotate_group_identifier import RotateGroupIdentifierPanel
from wallet.app.identifying.rotate_identifier import RotateIdentifierPanel
from wallet.app.identifying.view_identifer import ViewIdentifierPanel
from wallet.app.naving import Navbar
from wallet.app.witnessing.add_witness import AddWitness
from wallet.app.witnessing.view_witness import ViewWitness
from wallet.app.witnessing.witnesses import Witnesses
from wallet.notifying.notifications import Notifications

logger = logging.getLogger('wallet')


class Layout(ft.Row):
    def __init__(self, app, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = app
        self.page = page
        self.navbar = Navbar(page=page)
        self.notifications = Notifications(app)
        self.identifiers = identifying.Identifiers(app)
        self.contacts = contacting.Contacts(app)
        self.settings = settings.Settings(app)
        self.witnesses = Witnesses(app=self.app)
        self.splash = splashing.Splash(app)

        self._active_view = self.splash

        if self.app.agent is None:
            self.navbar.visible = False

        self.controls = [self.navbar, self.active_view]

    @property
    def active_view(self):
        return self._active_view

    @active_view.setter
    def active_view(self, view):
        self._active_view = view if view else self.splash
        self.controls[-1] = self._active_view

    def set_witness_view(self, aid):
        org = connecting.Organizer(hby=self.app.hby)
        witness = org.get(aid)
        self.active_view = ViewWitness(app=self.app, witness=witness)
        self.page.floating_action_button = None
        self.update()

    def set_witnesses_view(self):
        self.active_view = Witnesses(app=self.app)
        self.page.floating_action_button = ft.FloatingActionButton(icon=ft.Icons.ADD, on_click=self.witnesses.add_witness)
        self.update()

    def set_witness_add_view(self):
        self.active_view = AddWitness(app=self.app)
        self.page.floating_action_button = None
        self.update()

    def set_identifiers_list(self):
        self.active_view = self.identifiers
        self.page.floating_action_button = ft.FloatingActionButton(icon=ft.Icons.ADD, on_click=self.identifiers.add_identifier)
        self.navbar.rail.selected_index = Navbar.IDENTIFIERS
        self.navbar.update()
        self.update()

    def set_identifier_view(self, prefix):
        hab = self.app.hby.habs[prefix]
        self.active_view = ViewIdentifierPanel(self.app, hab)
        self.page.floating_action_button = None
        self.navbar.rail.selected_index = Navbar.IDENTIFIERS
        self.navbar.update()
        self.update()

    def set_identifier_rotate(self, prefix):
        hab = self.app.hby.habs[prefix]
        if isinstance(hab, habbing.GroupHab):
            self.active_view = RotateGroupIdentifierPanel(self.app, hab)
        else:
            self.active_view = RotateIdentifierPanel(self.app, hab)

        self.page.floating_action_button = None
        self.navbar.rail.selected_index = Navbar.IDENTIFIERS
        self.navbar.update()
        self.update()

    def set_identifier_create(self):
        self.active_view = CreateIdentifierPanel(self.app)
        self.navbar.rail.selected_index = Navbar.IDENTIFIERS
        self.navbar.update()
        self.update()

    def set_contact_create(self):
        self.active_view = CreateContactPanel(self.app)
        self.navbar.rail.selected_index = Navbar.CONTACTS
        self.navbar.update()
        self.update()

    def set_contacts_list(self):
        self.active_view = self.contacts
        self.page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.ADD,
            on_click=self.contacts.add_contact,
        )
        self.navbar.rail.selected_index = Navbar.CONTACTS
        self.navbar.update()
        self.page.update()

    def set_contact_view(self, aid):
        org = connecting.Organizer(hby=self.app.hby)
        contact = org.get(aid)
        self.active_view = ViewContactPanel(app=self.app, contact=contact)
        self.navbar.rail.selected_index = Navbar.CONTACTS
        self.navbar.update()
        self.page.update()

    def set_settings_view(self):
        self.active_view = self.settings
        self.navbar.rail.selected_index = Navbar.SETTINGS
        self.navbar.update()
        self.page.update()

    def set_notifications_view(self):
        self.active_view = Notifications(self.app)
        self.navbar.rail.selected_index = None
        self.navbar.update()
        self.page.update()

    def set_notifications_note_view(self, note_id):
        self.active_view = self.notifications.note_view(note_id)
        self.navbar.rail.selected_index = None
        self.navbar.update()
        self.page.update()

    def set_splash_view(self):
        self.splash.visible = True
        self.active_view = self.splash
        self.navbar.rail.selected_index = None
        self.navbar.update()
        self.page.update()

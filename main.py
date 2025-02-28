import ctypes
import logging.config
import os
import platform
import sys
from ctypes.util import find_library
from pathlib import Path

import certifi  # noqa - required so that the certifi package is included in the Flet build
import flet as ft
import uvloop
from flet.core.page import Page

from vendor import v_wsgiref  # noqa - required so that the v_wsgiref package is included in the Flet build
from wallet.app import colouring
from wallet.core import configing
from wallet.core.configing import WalletConfig
from wallet.storing import THEME_KEY

sys.modules['wsgiref'] = v_wsgiref  # noqa - required so that the wsgiref package is included in the Flet build

logging.config.fileConfig('logging.conf')
logger = logging.getLogger('wallet')

################################# Custom Libsodium Loader ############################################
# This code has to be in the main module to avoid a partially initialized module error
# for the 'wallet' module.


def load_custom_libsodium(appdir):
    """
    Instruct the pysodium library to load a custom libsodium dylib from the appdir/libsodium
    """
    set_load_path_or_link(appdir)
    set_load_env_vars(appdir)

    custom_path = os.path.expanduser(f'{os.path.dirname(os.path.abspath(__file__))}/libsodium/libsodium.dylib')
    logger.info(f'Loading custom libsodium from {custom_path}')
    if os.path.exists(custom_path):
        logger.info(f'Found custom libsodium at {custom_path}')
        ctypes.cdll.LoadLibrary(custom_path)
    else:
        logger.info('Custom libsodium not found, loading from system')
        libsodium_path = find_library('sodium')
        if libsodium_path is not None:
            logger.info(f'Found libsodium at {libsodium_path}')
            ctypes.cdll.LoadLibrary(libsodium_path)
            logger.info(f'Loaded libsodium from {libsodium_path}')
        else:
            raise OSError('libsodium not found')


def set_load_path_or_link(appdir):
    """
    Symlinks the correct libsodium dylib based on the architecture of the system.
    """
    lib_home = f'{appdir}/libsodium'
    arch = platform.processor()

    if platform.system() == 'Windows':
        match arch:
            case 'x86' | 'i386' | 'i486' | 'i586' | 'i686':
                sodium_lib = 'libsodium.26.x32.dll'
            case 'AMD64' | 'x86_64':
                sodium_lib = 'libsodium.26.x64.dll'
            case _:
                raise OSError(f'Unsupported Windows architecture: {arch}')
    elif platform.system() == 'Darwin':
        match platform.processor():
            case 'x86_64':
                sodium_lib = 'libsodium.26.x86_64.dylib'
            case 'arm' | 'arm64' | 'aarch64':
                sodium_lib = 'libsodium.23.arm.dylib'
            # doesn't work
            case 'i386':
                sodium_lib = 'libsodium.23.i386.dylib'
            case _:
                raise OSError(f'Unsupported architecture: {platform.processor()}')
    else:
        # Linux and other Unix-like systems
        raise OSError(f'Unsupported architecture: {platform.processor()}')

    lib_path = Path(os.path.join(lib_home, sodium_lib))

    logger.info(f'Arch: {platform.processor()} Linking libsodium lib: {sodium_lib} at path: {lib_path}')

    if platform.system() == 'Windows':  # if windows just set the PATH
        logger.info(f'Setting PATH to include {lib_path}')
        os.environ['PATH'] = f'{lib_path};{os.environ["PATH"]}'
    elif platform.system() == 'Darwin':  # if macOS, symlink the dylib
        if not lib_path.exists():
            logger.error(f'libsodium for architecture {platform.processor()} missing at {lib_path}, cannot link')
            raise FileNotFoundError(f'libsodium for architecture {platform.processor()} missing at {lib_path}')

        link_path = Path(os.path.join(lib_home, 'libsodium.dylib'))
        logger.info(f'Symlinking {lib_path} to {link_path}')
        try:
            os.symlink(f'{lib_path}', f'{link_path}')
        except FileExistsError:
            os.remove(f'{link_path}')
            os.symlink(f'{lib_path}', f'{link_path}')
        logger.info(f'Linked libsodium dylib: {link_path}')


def set_load_env_vars(appdir):
    """
    Sets the DYLD_LIBRARY_PATH and LD_LIBRARY_PATH that pysodium uses to find libsodium to the custom libsodium dylib.
    """
    if platform.system() == 'Windows':
        return  # Windows doesn't need this

    local_path = appdir

    logger.info(f'Setting DYLD_LIBRARY_PATH to {local_path}/libsodium')
    os.environ['DYLD_LIBRARY_PATH'] = f'{local_path}/libsodium'

    logger.info(f'Setting LD_LIBRARY_PATH to {local_path}/libsodium')
    os.environ['LD_LIBRARY_PATH'] = f'{local_path}/libsodium'


################################### End Custom Libsodium Loader ######################################


def wrap_with_config(config: WalletConfig):
    """
    Returns a function flet.app_async can run that closes over the config.
    """
    from wallet.app.apping import (
        WalletApp,
    )  # import here to allow changing libsodium path prior to importing keri classes which trigger libsodium import

    async def wallet_main(page: Page):
        """
        Main function for Wallet that has a reference to the config.
        """
        stored_theme = await page.client_storage.get_async(THEME_KEY)
        current_theme = stored_theme if stored_theme else page.platform_brightness.name
        await page.client_storage.set_async(THEME_KEY, current_theme)
        page.theme_mode = current_theme
        logger.info(f'Theme mode set to {current_theme}')
        clring = colouring.Colouring.set_theme(current_theme if current_theme != 'SYSTEM' else page.platform_brightness.name)

        page.fonts = {'monospace': config.font}
        page.padding = 0
        page.theme = colouring.Colouring.Light()
        page.dark_theme = colouring.Colouring.Dark()
        page.theme.floating_action_button_theme = colouring.Colouring.Light().FloatingActionButtonTheme()
        page.dark_theme.floating_action_button_theme = colouring.Colouring.Dark().FloatingActionButtonTheme()
        page.theme.page_transitions.macos = 'cupertino'
        page.update()

        app = WalletApp(page, config)
        page.add(app)
        app.colouring = clring
        page.end_drawer = app.agentDrawer

        logger.info('Wallet is running.')
        app.page.update()

    return wallet_main


async def launcher(config: WalletConfig):
    """Launches Wallet as a Flet async app."""
    await ft.app_async(target=wrap_with_config(config), assets_dir=config.assets_dir)


def run_wallet():
    """Entry point for app-level configuration and asyncio event loop."""
    uvloop.run(launcher(configing.read_config()))


if __name__ == '__main__':
    # get the directory of the main.py file so we can load the custom libsodium from ./libsodium
    appdir = os.path.dirname(os.path.abspath(__file__))
    load_custom_libsodium(appdir)
    run_wallet()

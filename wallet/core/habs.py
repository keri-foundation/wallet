import logging

from keri import kering
from keri.app import configing, habbing, keeping
from keri.core import coring, signing
from keri.vdr import credentialing

from wallet.core.agenting import runController

logger = logging.getLogger('wallet')


def format_bran(bran):
    if bran:
        bran = bran.replace('-', '')
    return bran


def check_passcode(name, base, bran, salt=None, tier=None, pidx=None, algo=None, seed=None):
    ks = keeping.Keeper(name=name, base=base, temp=False, reopen=True)
    aeid = ks.gbls.get('aeid')

    if bran and not seed:  # create seed from stretch of bran as salt
        if len(bran) < 21:
            raise ValueError('Bran (passcode seed material) too short.')
        bran = coring.MtrDex.Salt_128 + 'A' + bran[:21]  # qb64 salt for seed
        signer = signing.Salter(qb64=bran).signer(transferable=False, tier=None, temp=None)
        seed = signer.qb64
        if not aeid:  # aeid must not be empty event on initial creation
            aeid = signer.verfer.qb64  # lest it remove encryption

    if salt is None:  # salt for signing keys not aeid seed
        salt = signing.Salter(raw=b'0123456789abcdef').qb64
    else:
        salt = signing.Salter(qb64=salt).qb64

    try:
        keeping.Manager(ks=ks, seed=seed, aeid=aeid, pidx=pidx, algo=algo, salt=salt, tier=tier)
    except kering.AuthError as ex:
        raise ex
    ks.close()


def open_hby(name, base, bran, config_file, config_dir, app):
    """
    Opens a Habery.
    Returns the Agent and AsyncIO task running the HioTask for the Agent.
    """
    try:
        cf = None
        if config_file != '':
            cf = configing.Configer(name=config_file, base='', headDirPath=config_dir, temp=False, reopen=True, clear=False)
        hby = habbing.Habery(name=name, bran=bran, free=True, cf=cf)
    except kering.AuthError:
        logger.error(f'Passcode incorrect for {name}')
        raise
    except ValueError:
        logger.error(f'Open Habery failed on ValueError for {name}')
        raise
    rgy = credentialing.Regery(hby=hby, name=hby.name, base=base, temp=False)
    return runController(app=app, hby=hby, rgy=rgy)


def keystore_exists(name, base):
    """Checks if the keystore exists."""
    ks = keeping.Keeper(name=name, base=base, temp=False, reopen=True)
    aeid = ks.gbls.get('aeid')
    exists = aeid is not None
    ks.close()
    return exists

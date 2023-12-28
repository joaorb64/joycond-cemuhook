======
joycond-cemuhook
======

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu, etc.

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`_'s implementation.

Supports up to 4 controllers from the following:

- Joycon L+R combination (Select if motion comes from L or R Joycon)
- Switch Pro controller
- Left Joycon
- Right Joycon

How to install
--------

- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
- Run ``pip3 install git+https://github.com/joaorb64/joycond-cemuhook``

From now on, you'll only need to run ``joycond-cemuhook`` from a terminal.

- Connect your Nintendo Switch controllers and assign them as intended (using the respective L+R)
- Open a compatible emulator and enable cemuhook UDP motion input

Head to this project's `wiki <https://github.com/joaorb64/joycond-cemuhook/wiki>`_ for detailed instructions on how to configure Cemuhook on emulators.

How to run without installing
--------

- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
- Run `git clone git@github.com:joaorb64/joycond-cemuhook.git`
- `cd joycond-cemuhook/src/joycond_cemuhook` 
- Run with ``sudo python3 joycond-cemuhook/src/joycond_cemuhook``

Media
--------

The Legend of Zelda: Swkyward Sword on Dolphin

.. image:: ../media/zeldass-dolphin.gif


Mario Kart 8 on Cemu (Wine)

.. image:: ../media/mk8-cemu.gif

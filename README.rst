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

How to use
--------
- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
- Connect your Nintendo Switch controllers and assign them as intended (using the respective L+R)
- Run joycond-cemuhook: ``python3 joycond-cemuhook.py``
- Open a compatible emulator and enable cemuhook UDP motion input

Head to this project's `wiki <https://github.com/joaorb64/joycond-cemuhook/wiki>`_ for detailed instructions on how to configure Cemuhook on emulators.

Media
--------

The Legend of Zelda: Swkyward Sword on Dolphin

.. image:: ../media/zeldass-dolphin.gif


Mario Kart 8 on Cemu (Wine)

.. image:: ../media/mk8-cemu.gif

======
joycond-cemuhook
======

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu, etc.

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`_'s implementation.

Currently supports one connected controller from the following:

- Joycon L+R combination. It detects and uses only the Right joycon's motion as to simulate a Wii Remote
- Switch Pro controller

How to use
--------
- Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
- Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
- Connect your Nintendo Switch controllers and assign them as intended (using L+R)
- Run joycond-cemuhook: ``python3 joycond-cemuhook``
- Open a compatible emulator and enable cemuhook UDP motion input

Media
--------

The Legend of Zelda: Swkyward Sword on Dolphin

.. image:: ../media/zeldass-dolphin.gif


Mario Kart 8 on Cemu (Wine)

.. image:: ../media/mk8-cemu.gif

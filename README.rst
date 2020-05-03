================
joycond-cemuhook
================

Support for cemuhook's UDP protocol for joycond devices for use with emulators like Dolphin, Cemu, Citra, Yuzu, etc.

Server code heavly based on `ds4drv-cemuhook <https://github.com/TheDrHax/ds4drv-cemuhook>`_'s implementation.

Supports up to 4 controllers from the following:

- Joycon L+R combination (Select if motion comes from L or R Joycon)
- Switch Pro controller
- Left Joycon
- Right Joycon

How to use
----------
1. Install `dkms-hid-nintendo <https://github.com/nicman23/dkms-hid-nintendo>`_ (if your kernel doesn't include the hid_nintendo driver)
2. Install the `joycond <https://github.com/DanielOgorchock/joycond>`_ userspace driver
3. Connect your Nintendo Switch controllers and assign them as intended (using the respective L+R)
4. Run joycond-cemuhook: ``python3 joycond-cemuhook.py``
5. Open a compatible emulator and enable cemuhook UDP motion input

Systemd unit
------------
Instead of manually running ``joycond-cemuhook.py`` you can use the provided systemd-unit to start it automatically:

::

  # cp joycond-cemuhook.py /usr/local/bin/
  $ cp joycond-cemuhook.service ~/.config/systemd/user/
  $ systemctl --user enable --now joycond-cemuhook.service


Media
-----

The Legend of Zelda: Swkyward Sword on Dolphin

.. image:: ../media/zeldass-dolphin.gif


Mario Kart 8 on Cemu (Wine)

.. image:: ../media/mk8-cemu.gif

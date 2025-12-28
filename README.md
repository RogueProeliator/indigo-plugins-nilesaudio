# Niles Audio Receiver Plugin for Indigo

Control your Niles Audio multi-zone receiver from Indigo Home Automation.

## Version 2025.4.2

This is a complete rewrite of the Niles Audio Receiver plugin for Indigo 2025.1, removing the dependency on RPFramework and bringing the codebase inline with modern Indigo plugin standards. 2025.2 adds support for treating Zones as dimmers, allowing volume control inside of native clients.

## Features

- **Multi-Zone Control** - Control up to 18 zones across ZR-4/ZR-6 receivers
- **Zone Power** - Turn individual zones on/off or all zones at once
- **Volume Control** - Set volume levels with dimmer-style controls (brightness = volume)
- **Source Selection** - Switch between up to 6 audio sources per zone
- **Mute Control** - Mute/unmute individual zones
- **Tuner Control** - Tune to AM/FM radio stations
- **Bass & Treble** - Read bass and treble levels from zones
- **Status Polling** - Automatic status updates from the receiver
- **Dimmer Device Support** - Zone devices work as dimmers for native Indigo volume control

## Requirements

- Indigo 2025.1 or later
- Python 3.10+ (included with Indigo)
- Serial connection to Niles Audio ZR-4 or ZR-6 receiver

## Upgrading from Previous Versions

If upgrading from a version prior to 2025.5.0, use the menu item **Plugins → Niles Audio Receiver → Upgrade Zone Devices to Dimmer Type** to convert your zone devices to the new dimmer-based type for native volume control.

## Support

- [Full Documentation](https://github.com/RogueProeliator/indigo-plugins-nilesaudio/wiki)
- [GitHub Repository](https://github.com/RogueProeliator/indigo-plugins-nilesaudio)
- [Help Forum](https://forums.indigodomo.com/viewforum.php?f=62)

## License

MIT License - See LICENSE.txt for details.

## Credits

Developed by RogueProeliator <rp@rogueproeliator.com>

---

_**Previous Indigo Releases**_  
[v2.2.0 Plugin for Indigo 6 - 2022.2](https://github.com/RogueProeliator/indigo-plugins-nilesaudio/releases/tag/v2.2.0)
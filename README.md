#Introduction
This Indigo 6.0+ plugin allows Indigo to control a Niles Audio Multi-Zone Receiver (ZR-4 or ZR-6) without the need to purchase additional keypads or control points from Niles Audio (though the plugin can work in tandem with those as well.) This plugin connects via a serial connection to the Niles Audio Receiver and is able to both read and set nearly all aspects of the device.

#Hardware Requirements
This plugin should work with both the four and six zone receivers, ZR-4 and ZR-6 respectively, from Niles Audio. It does not require any additional hardware (such as keypads or touchscreens) for setup or operation... in fact, this plugin was originally conceived and built to avoid spending additional money on those! The plugin is able to control up to two additional slave ZR-6 receivers, allowing up to 18 zones of distributed audio.

#Installation and Configuration
###Obtaining the Plugin
The latest released version of the plugin is available for download in the Releases section... those versions in beta will be marked as a Pre-Release and will not appear in update notifications.

###Configuring the Plugin
Upon first installation you will be asked to configure the plugin; please see the instructions on the configuration screen for more information. Most users will be fine with the defaults unless an email is desired when a new version is released.
![](<Documentation/Doc-Images/PluginConfig.png>)

#Plugin Devices
###Receiver Devices
You will need to create a new Niles Audio Receiver device in Indigo for each **MASTER** receiver; note that all control of slaves devices goes through the master, so only one receiver should be created in Indigo. In the Device Settings you will need to set a couple of options such as selecting the serial port and, optionally, adding source information / labels:
![](<Documentation/Doc-Images/ReceiverDeviceConfig.png>)

###Audio Zones
After the receiver has been created, you must create an Indigo device for each zone attached to the receiver (or its slave receivers). In the Device Settings you will need to set a couple of options including selecting the receiver to which this zone is attached and the number of the zone (on the receiver). Note that slave receivers continue the numbering scheme from the master, so for ZR-6 receivers the master's zones are numbered 1 to 6, the first slave 7 to 12 and the second slave 13 to 18:
![](<Documentation/Doc-Images/ZoneDeviceConfig.png>)

#Available Actions
###Party Zones - All Off / Mute All
These two actions will turn off or mute all of the zones on the receiver that have been marked as part of the Party designation; by default if you have not setup this feature it will act on all zones.

###Tune to Radio Station
This will tune the receiver's radio station to the station requested; the entry may be in AM (XXXX) or FM (XXX.X) format.

###Zone Power - Toggle / Set
These actions control the Power On/Off state of a zone... Please note that the Toggle actually relies on the current state of the zone within the plugin, so if you have additional controls outside of Indigo (such as zone keypads) then be sure to enable the status polling in the receiver's device configuration screen.

###Set Zone Source
This action allows selection of the source of the audio to play on the zone.

###Zone Volume - Set Value / Adjust Value
This action will change the volume of the zone; the adjustment simply increments or decrements the volume similar to an up/down volume button. The Set value will allow you to set the volume to a specific value, but it must do so by internally using incremental up/down volume calls (this is a limitation of the Niles Audio control interface). To do this requires the current state of the zone so, as with the power, it is important to setup the status polling if you have external controls that may change the volume level without Indigo knowing.

Please note that the Niles Audio Receivers prevent a rapid succession of commands, so adjusting the volume by large amounts is sometimes slow (it takes about 0.15 seconds to execute each unit of change).

###Zone Mute - Toggle / Set
These actions control the Mute On/Off state of a zone... Note that the Set action actually relies on the current state of the zone within the plugin since the receiver does not provide discrete on/off controls, so if you have additional controls outside of Indigo (such as zone keypads) then be sure to enable the status polling in the receiver's device configuration screen.

#Available Device States
This plugin will track the current status/state of many of the properties of the zones attached to the device - such as power status, mute, volume, current source, bass and treble levels.

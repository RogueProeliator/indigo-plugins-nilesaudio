#! /usr/bin/env python
# -*- coding: utf-8 -*-
#/////////////////////////////////////////////////////////////////////////////////////////
# Niles Audio Receiver Plugin by RogueProeliator <rp@rogueproeliator.com>
# 	Indigo plugin designed to allow full control of a Niles Audio receiver such as the
#	ZR-4 and ZR-6
#	
#	Command structure based on Niles Audio's published specification found in the ZR-6
#	instruction manual
#
#/////////////////////////////////////////////////////////////////////////////////////////


#/////////////////////////////////////////////////////////////////////////////////////////
# Python imports
#/////////////////////////////////////////////////////////////////////////////////////////
import re
import string
import os

import RPFramework
import nilesAudioDevices


#/////////////////////////////////////////////////////////////////////////////////////////
# Constants and configuration variables
#/////////////////////////////////////////////////////////////////////////////////////////


#/////////////////////////////////////////////////////////////////////////////////////////
# Plugin
#	Primary Indigo plugin class that is universal for all receiver devices to be
#	controlled (this represents the master receiver)
#/////////////////////////////////////////////////////////////////////////////////////////
class Plugin(RPFramework.RPFrameworkPlugin.RPFrameworkPlugin):
	
	#/////////////////////////////////////////////////////////////////////////////////////
	# Class construction and destruction methods
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class creation; setup the device tracking
	# variables for later use
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
		# RP framework base class's init method
		super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs, managedDeviceClassModule=nilesAudioDevices)
	
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-	
	# This routine will be called from the user executing the menu item action to send
	# an arbitrary command code to the Niles Audio receiver
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-	
	def sendArbitraryCommand(self, valuesDict, typeId):
		try:
			deviceId = valuesDict.get("targetDevice", "0")
			commandCode = valuesDict.get("commandToSend", "").strip()
		
			if deviceId == "" or deviceId == "0":
				# no device was selected
				errorDict = indigo.Dict()
				errorDict["targetDevice"] = "Please select a device"
				return (False, valuesDict, errorDict)
			elif commandCode == u'':
				errorDict = indigo.Dict()
				errorDict["commandToSend"] = "Enter command to send"
				return (False, valuesDict, errorDict)
			else:
				# send the code using the normal action processing...
				actionParams = indigo.Dict()
				actionParams["commandCode"] = commandCode
				self.executeAction(pluginAction=None, indigoActionId="SendArbitraryCommand", indigoDeviceId=int(deviceId), paramValues=actionParams)
				return (True, valuesDict)
		except:
			self.exceptionLog()
			return (False, valuesDict)
			
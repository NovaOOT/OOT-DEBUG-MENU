// The Discord application OOT-PC's Rich Presence (discord_rpc.c) speaks for: the one place its defaults live.
//
// Discord shows the *application's* name above the presence ("Playing <name>") and takes the large image from the
// art assets uploaded to that application; neither comes from the game. So the presence needs an application of its
// own, registered at discord.com/developers/applications under the name "The Legend of Zelda: Ocarina of Time PC", with its art uploaded under
// Rich Presence > Art Assets with the key below. Its Application ID (General Information) goes into
// OOTPC_DISCORD_APP_ID_DEFAULT; a player can still point at another application with discord_app_id and
// discord_image in OOT-PC.ini ([discord]), which take precedence over these defaults.
//
// While the id is empty and OOT-PC.ini names none, the presence stays off even with discord_rpc = 1 and logs why:
// an id of some other application would show that application's name instead of OOT-PC.
#pragma once

#define OOTPC_DISCORD_APP_ID_DEFAULT "1571728936977571971"   // the OOT-PC application's id: digits, at most 20
#define OOTPC_DISCORD_IMAGE_DEFAULT "icon"                   // its large-image art asset key (art/icon.jpeg)

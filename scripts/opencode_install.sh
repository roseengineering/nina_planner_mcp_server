# Sample Script I use to install a background service for OpenCode on macOS
# This sets up a LaunchAgent plist for the service and ensures it runs at load
# It also configures environment variables and program arguments for the service
# WorkingDirectory sets where /new starts a new session
# Run this script in the directory where you want the service to use as its working directory   
service=local.opencode
cat <<EOF > ~/Library/LaunchAgents/$service.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${service}</string>
    <key>StandardOutPath</key>
    <string>/tmp/${service}.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/${service}.log</string>
    <key>WorkingDirectory</key>
    <string>$(pwd)</string> 
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin</string>
        <key>NPM_CONFIG_USERCONFIG</key>
        <string>$HOME/.config/npm/npmrc</string>
        <key>OPENCODE_SERVER_PASSWORD</key>
        <string>passw0rd</string>
        <key>PYTHONDONTWRITEBYTECODE</key>
        <string>1</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>$HOME/.local/bin/opencode</string>
        <string>serve</string>
        <string>--port</string>
        <string>4096</string>
    </array>
</dict>
</plist>
EOF
sudo rm -f /tmp/${service}.log
cat ~/Library/LaunchAgents/$service.plist
plutil -lint ~/Library/LaunchAgents/$service.plist
launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/$service.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$service.plist
launchctl list | grep local

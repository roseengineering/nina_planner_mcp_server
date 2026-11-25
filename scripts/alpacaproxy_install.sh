service=local.alpacaproxy

if [ "$1" = "list" ]; then
  launchctl list | grep 'local\.' | grep --color=always -E "$service|\$" 

elif [ "$1" = "restart" ]; then
  cat ~/Library/LaunchAgents/$service.plist
  launchctl kickstart -k gui/$(id -u)/$service
  launchctl list | grep 'local\.' | grep --color=always -E "$service|\$" 

elif [ "$1" = "uninstall" ]; then
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/$service.plist
  launchctl list | grep 'local\.' | grep --color=always -E "$service|\$" 
  rm -f ~/Library/LaunchAgents/$service.plist
  rm -f /tmp/$service.log

elif [ "$1" = "log" ]; then
  cat /tmp/$service.log

elif [ "$1" = "install" ]; then
  rm -rf ~/.local/share/alpacaproxy
  mkdir -p ~/.local/share/alpacaproxy
  cp alpacaproxy.py ~/.local/share/alpacaproxy
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
    <string>${HOME}/.local/share/alpacaproxy</string> 
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin</string>
        <key>PYTHONDONTWRITEBYTECODE</key>
        <string>1</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>$HOME/.local/bin/uv</string>
        <string>run</string>
        <string>--with</string>
        <string>httpx</string>
        <string>alpacaproxy.py</string>
        <string>--listen-host</string>
        <string>0.0.0.0</string>
    </array>
</dict>
</plist>
EOF
  sudo rm -f /tmp/${service}.log
  cat ~/Library/LaunchAgents/$service.plist
  plutil -lint ~/Library/LaunchAgents/$service.plist
  launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/$service.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$service.plist
  launchctl list | grep 'local\.' | grep --color=always -E "$service|\$" 

else
  echo 'accepts: list restart uninstall install'
fi



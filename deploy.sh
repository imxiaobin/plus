#!/usr/bin/expect -f
# SSH部署脚本

set timeout 60
set password "8J9w63OllDynyV2Wd98P"
set host "204.152.197.132"
set user "root"

spawn ssh -o StrictHostKeyChecking=accept-new ${user}@${host}

expect {
    "password:" {
        send "${password}\r"
        exp_continue
    }
    "# " {
        send "cd /opt/abaifreegpt\r"
        expect "# "

        send "git fetch origin\r"
        expect "# "

        send "git reset --hard origin/main\r"
        expect "# "

        send "cd frontend\r"
        expect "# "

        send "npm ci\r"
        expect "# "

        send "npm run build\r"
        expect "# "

        send "cd ..\r"
        expect "# "

        send "systemctl restart abaifreegpt.service\r"
        expect "# "

        send "sleep 3\r"
        expect "# "

        send "systemctl status abaifreegpt.service\r"
        expect "# "

        send "curl http://127.0.0.1:8094/api/health\r"
        expect "# "

        send "exit\r"
    }
}

expect eof

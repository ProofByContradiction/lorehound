# lorehound.zsh — management command for the Lorehound Discord bot.
#
# Enable by adding this line to ~/.zshrc:
#     source "/Users/mckeema/Documents/Coding Work/lorehound/lorehound.zsh"
#
# Then from anywhere:  lorehound start | stop | restart | status | logs | run
#
# Resolve the repo dir from THIS file's own location, so moving the repo only
# means updating the one path in ~/.zshrc (zsh-only: %x = path of sourced file).
LOREHOUND_DIR="${${(%):-%x}:A:h}"

lorehound() {
    local dir="$LOREHOUND_DIR"
    local log="$dir/bot.log" pidf="$dir/.bot.pid"
    case "${1:-start}" in
        start)
            if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
                echo "lorehound: already running (pid $(cat "$pidf"))"; return 0
            fi
            nohup "$dir/run.sh" >> "$log" 2>&1 &
            echo $! > "$pidf"
            sleep 1
            if kill -0 "$(cat "$pidf")" 2>/dev/null; then
                echo "lorehound: started (pid $(cat "$pidf")) -> logging to $log"
            else
                echo "lorehound: failed to start — check $log"; rm -f "$pidf"
            fi
            ;;
        stop)
            if [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null; then
                rm -f "$pidf"; echo "lorehound: stopped"
            else
                echo "lorehound: not running"; rm -f "$pidf"
            fi
            ;;
        restart) lorehound stop; lorehound start ;;
        status)
            if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
                echo "lorehound: running (pid $(cat "$pidf"))"
            else
                echo "lorehound: stopped"
            fi
            ;;
        logs) tail -f "$log" ;;
        run)  "$dir/run.sh" ;;
        help|-h|--help)
            echo "usage: lorehound {start|stop|restart|status|logs|run}"
            echo "  start    run in background (default), logs to bot.log"
            echo "  stop     stop the background bot"
            echo "  restart  stop then start"
            echo "  status   running/stopped + pid"
            echo "  logs     tail -f bot.log (Ctrl-C exits the tail, not the bot)"
            echo "  run      run in the foreground (Ctrl-C stops the bot)"
            ;;
        *) echo "lorehound: unknown command '$1' (try: lorehound help)"; return 1 ;;
    esac
}

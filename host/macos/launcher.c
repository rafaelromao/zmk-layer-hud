/* The executable of ZMK Layer HUD.app, the login item `zmk-layer-hud autostart enable` builds.
 *
 * It runs the command it is given (argv[1] on) as its child and waits for it, passing on TERM,
 * INT and HUP, and exits as the child did. It exists for macOS's privacy checks: those ask the
 * *responsible* app, which a child inherits from its parent. From a terminal that is the terminal,
 * whose Info.plist says why a program in it may use Bluetooth. Started by launchd as plain
 * Python, it would be Homebrew's Python.app, which says no such thing, so the feed would lose the
 * Bluetooth keyboard. Here it is this app, with its own purpose string and its own Input
 * Monitoring grant.
 *
 * So it must stay the parent: exec'ing the command would make this process Python again.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static volatile pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s COMMAND [ARG...]  (zmk-layer-hud's login item runs this)\n", argv[0]);
        return 64;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    pid_t pid;
    int err = posix_spawn(&pid, argv[1], NULL, NULL, argv + 1, environ);
    if (err != 0) {
        fprintf(stderr, "%s: %s\n", argv[1], strerror(err));
        return 127;
    }
    child = pid;
    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) return 1;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 128 + WTERMSIG(status);
}

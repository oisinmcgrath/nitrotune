/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/types.h>
#include <unistd.h>

#define EC_IO_PATH "/sys/kernel/debug/ec/ec0/io"

enum {
    CPU_FAN_LOW = 0x13,
    CPU_FAN_HIGH = 0x14,
    GPU_FAN_LOW = 0x15,
    GPU_FAN_HIGH = 0x16,
    HELPER_TIMEOUT_SECONDS = 3
};

static int
fail(const char *message)
{
    (void)fprintf(stderr, "nitrotune-ec-read: %s\n", message);
    return 1;
}

static int
arm_helper_timeout(void)
{
    struct sigaction action = {0};
    sigset_t alarm_set;

    action.sa_handler = SIG_DFL;
    if (sigemptyset(&action.sa_mask) < 0 ||
        sigaction(SIGALRM, &action, NULL) < 0) {
        return -1;
    }

    if (sigemptyset(&alarm_set) < 0 ||
        sigaddset(&alarm_set, SIGALRM) < 0 ||
        sigprocmask(SIG_UNBLOCK, &alarm_set, NULL) < 0) {
        return -1;
    }

    (void)alarm(HELPER_TIMEOUT_SECONDS);
    return 0;
}

static int
read_byte_at(int fd, off_t offset, unsigned char *value)
{
    off_t position;
    ssize_t count;

    do {
        position = lseek(fd, offset, SEEK_SET);
    } while (position < 0 && errno == EINTR);

    if (position != offset) {
        return -1;
    }

    do {
        count = read(fd, value, 1);
    } while (count < 0 && errno == EINTR);

    return count == 1 ? 0 : -1;
}

static int
write_all(int fd, const char *data, size_t length)
{
    size_t written_total = 0;

    while (written_total < length) {
        ssize_t written = write(
            fd,
            data + written_total,
            length - written_total
        );

        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return -1;
        }

        written_total += (size_t)written;
    }

    return 0;
}

int
main(int argc, char *argv[])
{
    int fd;
    unsigned char cpu_low;
    unsigned char cpu_high;
    unsigned char gpu_low;
    unsigned char gpu_high;
    unsigned int cpu_rpm;
    unsigned int gpu_rpm;
    char output[64];
    int output_length;

    (void)argv;

    if (argc != 1) {
        return fail("arguments are not accepted");
    }

    if (geteuid() != 0) {
        return fail("must be invoked through the authorized sudo rule");
    }

    if (arm_helper_timeout() < 0) {
        return fail("cannot arm helper timeout");
    }

    fd = open(EC_IO_PATH, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return fail("cannot open EC interface");
    }

    if (read_byte_at(fd, CPU_FAN_LOW, &cpu_low) < 0 ||
        read_byte_at(fd, CPU_FAN_HIGH, &cpu_high) < 0 ||
        read_byte_at(fd, GPU_FAN_LOW, &gpu_low) < 0 ||
        read_byte_at(fd, GPU_FAN_HIGH, &gpu_high) < 0) {
        (void)close(fd);
        return fail("cannot read required EC registers");
    }

    if (close(fd) < 0) {
        return fail("cannot close EC interface");
    }

    cpu_rpm = ((unsigned int)cpu_high << 8) | cpu_low;
    gpu_rpm = ((unsigned int)gpu_high << 8) | gpu_low;

    output_length = snprintf(
        output,
        sizeof(output),
        "CPU_RPM=%u\nGPU_RPM=%u\n",
        cpu_rpm,
        gpu_rpm
    );
    if (output_length < 0 || (size_t)output_length >= sizeof(output)) {
        return fail("cannot format result");
    }

    if (write_all(STDOUT_FILENO, output, (size_t)output_length) < 0) {
        return fail("cannot write complete result");
    }

    return 0;
}

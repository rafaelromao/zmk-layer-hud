#!/usr/bin/env python3
"""macOS diagnostic: why can't we open the ZMK keyboard's HID device?

Opens every matching IOHIDDevice through IOKit directly (no hidapi) and prints the raw
IOReturn code, which names the blocker:
  0x00000000  success                  -> hidapi should work; report the hidapi version
  0xe00002c5  kIOReturnExclusiveAccess -> another process seized it (Karabiner-Elements grabber)
  0xe00002e2  kIOReturnNotPermitted    -> Input Monitoring missing for this app
  0xe00002bc  kIOReturnError           -> generic; usually TCC as well

    /opt/homebrew/bin/python3 host/hiddiag.py [vid] [pid]
"""

import ctypes
import ctypes.util
import sys

cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

CFTypeRef = ctypes.c_void_p
cf.CFNumberCreate.restype = CFTypeRef
cf.CFNumberCreate.argtypes = [CFTypeRef, ctypes.c_long, ctypes.c_void_p]
cf.CFStringCreateWithCString.restype = CFTypeRef
cf.CFStringCreateWithCString.argtypes = [CFTypeRef, ctypes.c_char_p, ctypes.c_uint32]
cf.CFDictionaryCreateMutable.restype = CFTypeRef
cf.CFDictionaryCreateMutable.argtypes = [CFTypeRef, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
cf.CFDictionarySetValue.argtypes = [CFTypeRef, CFTypeRef, CFTypeRef]
cf.CFSetGetCount.restype = ctypes.c_long
cf.CFSetGetCount.argtypes = [CFTypeRef]
cf.CFSetGetValues.argtypes = [CFTypeRef, ctypes.POINTER(CFTypeRef)]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [CFTypeRef, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [CFTypeRef, ctypes.c_long, ctypes.c_void_p]
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFGetTypeID.argtypes = [CFTypeRef]
cf.CFStringGetTypeID.restype = ctypes.c_ulong

iokit.IOHIDManagerCreate.restype = CFTypeRef
iokit.IOHIDManagerCreate.argtypes = [CFTypeRef, ctypes.c_uint32]
iokit.IOHIDManagerSetDeviceMatching.argtypes = [CFTypeRef, CFTypeRef]
iokit.IOHIDManagerCopyDevices.restype = CFTypeRef
iokit.IOHIDManagerCopyDevices.argtypes = [CFTypeRef]
iokit.IOHIDDeviceOpen.restype = ctypes.c_uint32
iokit.IOHIDDeviceOpen.argtypes = [CFTypeRef, ctypes.c_uint32]
iokit.IOHIDDeviceClose.restype = ctypes.c_uint32
iokit.IOHIDDeviceClose.argtypes = [CFTypeRef, ctypes.c_uint32]
iokit.IOHIDDeviceGetProperty.restype = CFTypeRef
iokit.IOHIDDeviceGetProperty.argtypes = [CFTypeRef, CFTypeRef]
iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt32Type = 3
kCFNumberSInt64Type = 4

NAMES = {0: "kIOReturnSuccess", 0xE00002C5: "kIOReturnExclusiveAccess (seized by another process, e.g. Karabiner)",
         0xE00002E2: "kIOReturnNotPermitted (Input Monitoring missing)", 0xE00002BC: "kIOReturnError",
         0xE00002C2: "kIOReturnUnsupported", 0xE00002CD: "kIOReturnNotOpen"}


def cfstr(s):
    return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def cfnum(n):
    v = ctypes.c_int32(n)
    return cf.CFNumberCreate(None, kCFNumberSInt32Type, ctypes.byref(v))


def prop(dev, key):
    v = iokit.IOHIDDeviceGetProperty(dev, cfstr(key))
    if not v:
        return None
    if cf.CFGetTypeID(v) == cf.CFStringGetTypeID():
        buf = ctypes.create_string_buffer(256)
        cf.CFStringGetCString(v, buf, 256, kCFStringEncodingUTF8)
        return buf.value.decode(errors="replace")
    n = ctypes.c_int64(0)
    cf.CFNumberGetValue(v, kCFNumberSInt64Type, ctypes.byref(n))
    return n.value


def main():
    vid = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x1D50
    pid = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x615E
    print("Input Monitoring for this app:", {0: "granted", 1: "denied", 2: "unknown"}.get(iokit.IOHIDCheckAccess(1)))
    mgr = iokit.IOHIDManagerCreate(None, 0)
    match = cf.CFDictionaryCreateMutable(None, 0, None, None)
    cf.CFDictionarySetValue(match, cfstr("VendorID"), cfnum(vid))
    cf.CFDictionarySetValue(match, cfstr("ProductID"), cfnum(pid))
    iokit.IOHIDManagerSetDeviceMatching(mgr, match)
    devs = iokit.IOHIDManagerCopyDevices(mgr)
    if not devs:
        print(f"no HID device with {vid:04x}:{pid:04x}")
        return 1
    n = cf.CFSetGetCount(devs)
    arr = (CFTypeRef * n)()
    cf.CFSetGetValues(devs, arr)
    for dev in arr:
        product = prop(dev, "Product")
        transport = prop(dev, "Transport")
        page, usage = prop(dev, "PrimaryUsagePage"), prop(dev, "PrimaryUsage")
        rc = iokit.IOHIDDeviceOpen(dev, 0)
        print(f"{product} [{transport}] usage {page}/{usage}: open -> 0x{rc:08x} {NAMES.get(rc, '')}")
        if rc == 0:
            iokit.IOHIDDeviceClose(dev, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())

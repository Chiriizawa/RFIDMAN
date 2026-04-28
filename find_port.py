import serial.tools.list_ports

print("Available COM ports:")
ports = list(serial.tools.list_ports.comports())

if not ports:
    print("❌ No COM ports found! Check if Arduino is connected.")
else:
    for port in ports:
        print(f"  {port.device} - {port.description}")
        if "arduino" in port.description.lower() or "ch340" in port.description.lower():
            print(f"    >>> THIS IS YOUR ARDUINO! Use {port.device}")
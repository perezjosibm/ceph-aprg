# Setting Random Block Manager for SeaStore in Crimson OSD

## Configuration Option

To enable the Random Block Manager (RBM) for SeaStore in Crimson OSD, use the configuration option:

**Option**: [`seastore_main_device_type`](../ceph/src/common/options/crimson.yaml.in:232)

**Location**: [`crimson.yaml.in:232-236`](../ceph/src/common/options/crimson.yaml.in:232-236)

```yaml
- name: seastore_main_device_type
  type: str
  level: dev
  desc: The main device type seastore uses (SSD or RANDOM_BLOCK_SSD)
  default: SSD
```

## Setting the Configuration

### Method 1: Using `ceph config set`

```bash
# For a specific Crimson OSD
ceph config set osd.X seastore_main_device_type RANDOM_BLOCK_SSD

# For all Crimson OSDs
ceph config set osd seastore_main_device_type RANDOM_BLOCK_SSD

# Globally (affects all new OSDs)
ceph config set global seastore_main_device_type RANDOM_BLOCK_SSD
```

### Method 2: In Configuration File

Add to `ceph.conf`:
```ini
[osd]
seastore_main_device_type = RANDOM_BLOCK_SSD

# Or for specific OSD
[osd.X]
seastore_main_device_type = RANDOM_BLOCK_SSD
```

### Method 3: At OSD Creation Time

```bash
# Set before creating the OSD
ceph-osd --mkfs --seastore_main_device_type=RANDOM_BLOCK_SSD ...
```

## Valid Values

[`string_to_device_type()`](../ceph/src/crimson/os/seastore/seastore_types.cc:864-878) accepts:

1. **`"SSD"`** (default) - Uses segmented storage backend
2. **`"RANDOM_BLOCK_SSD"`** - Uses Random Block Manager
3. `"HDD"` - For spinning disks (segmented)
4. `"ZBD"` - For Zoned Block Devices

## Code Flow

### 1. Configuration Reading
[`seastore.cc:277-281`](../ceph/src/crimson/os/seastore/seastore.cc:277-281)
```cpp
std::string type = get_conf<std::string>("seastore_main_device_type");
device_type_t d_type = string_to_device_type(type);
assert(d_type == device_type_t::SSD ||
       d_type == device_type_t::RANDOM_BLOCK_SSD);
```

### 2. Device Type Parsing
[`seastore_types.cc:864-878`](../ceph/src/crimson/os/seastore/seastore_types.cc:864-878)
```cpp
device_type_t string_to_device_type(std::string type) {
  if (type == "SSD") {
    return device_type_t::SSD;
  }
  if (type == "RANDOM_BLOCK_SSD") {
    return device_type_t::RANDOM_BLOCK_SSD;
  }
  // ... other types
  return device_type_t::NONE;
}
```

### 3. Device Creation
[`device.cc:116-130`](../ceph/src/crimson/os/seastore/device.cc:116-130)
```cpp
seastar::future<DeviceRef>
Device::make_device(const std::string& device, device_type_t dtype)
{
  if (get_default_backend_of_device(dtype) == backend_type_t::SEGMENTED) {
    return SegmentManager::get_segment_manager(device, dtype);
  } 
  assert(get_default_backend_of_device(dtype) == backend_type_t::RANDOM_BLOCK);
  return get_rb_device(device);  // Creates Random Block Manager
}
```

### 4. RBM Device Instantiation
[`random_block_manager.cc:11-19`](../ceph/src/crimson/os/seastore/random_block_manager.cc:11-19)
```cpp
seastar::future<random_block_device::RBMDeviceRef>
get_rb_device(const std::string &device)
{
  return seastar::make_ready_future<random_block_device::RBMDeviceRef>(
    std::make_unique<
      random_block_device::nvme::NVMeBlockDevice
    >(device + "/block"));
}
```

## Related Configuration

When using `RANDOM_BLOCK_SSD`, you may also want to configure:

**[`seastore_cbjournal_size`](../ceph/src/common/options/crimson.yaml.in:237-241)**
```yaml
- name: seastore_cbjournal_size
  type: size
  level: dev
  desc: Total size to use for CircularBoundedJournal if created, 
        it is valid only if seastore_main_device_type is RANDOM_BLOCK
  default: 5_G
```

Set via:
```bash
ceph config set osd.X seastore_cbjournal_size 10G
```

## Backend Types

SeaStore supports two backend types:

1. **SEGMENTED** (`device_type_t::SSD`, `HDD`, `ZBD`)
   - Uses [`SegmentManager`](../ceph/src/crimson/os/seastore/segment_manager.cc:32-34)
   - Log-structured storage
   - Append-only writes to segments

2. **RANDOM_BLOCK** (`device_type_t::RANDOM_BLOCK_SSD`)
   - Uses [`RandomBlockManager`](../ceph/src/crimson/os/seastore/random_block_manager.cc:11-19)
   - Random access storage
   - In-place updates possible
   - Uses CircularBoundedJournal

## Verification

After setting the configuration, verify it's applied:

```bash
# Check current value
ceph config get osd.X seastore_main_device_type

# Check effective configuration
ceph daemon osd.X config show | grep seastore_main_device_type

# View in OSD logs
# Look for device type initialization messages
```

## Important Notes

1. **Must be set before OSD creation** - This setting determines the storage backend format and cannot be changed after the OSD is created without reformatting

2. **Requires compatible hardware** - Random Block Manager is optimized for NVMe devices with random access capabilities

3. **Development feature** - The `level: dev` indicates this is a development/experimental feature

4. **Journal size matters** - When using `RANDOM_BLOCK_SSD`, ensure [`seastore_cbjournal_size`](../ceph/src/common/options/crimson.yaml.in:240) is appropriately sized for your workload

## Summary

To use Random Block Manager with SeaStore:
```bash
ceph config set osd.X seastore_main_device_type RANDOM_BLOCK_SSD
ceph config set osd.X seastore_cbjournal_size 10G  # Optional, adjust as needed
```

This configuration must be set **before** creating the OSD, as it determines the fundamental storage backend architecture.


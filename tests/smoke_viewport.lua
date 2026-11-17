local host = require "mp"

local function verify()
    local events, hooks, observers = {}, {}, {}
    local timer
    local input = {label = "svp-input", params = {graph = "scale=w=iw:h=ih,format=pix_fmts=yuv420p|yuv444p"}}
    local source_graph = input.params.graph
    local output = {label = "svp-interpolate"}
    local user_filter = {label = "user-filter", enabled = false}
    local filters = {input, output, user_filter}
    local properties = {
        vf = filters,
        ["track-list"] = {{type = "video", id = 1, ["demux-w"] = 3840, ["demux-h"] = 2160}},
        ["video-dec-params"] = {w = 3840, h = 2160},
        ["video-target-params"] = {w = 2528, h = 1422},
        ["osd-dimensions"] = {w = 2528, h = 1600},
    }
    local updates = 0
    local function get(name, default)
        local value = properties[name]
        if value == nil then return default end
        return value
    end
    package.loaded.mp = {
        get_property = get,
        get_property_native = get,
        get_property_number = get,
        set_property = function(name, value) properties[name] = value end,
        set_property_native = function(name, value)
            if name == "vf" then
                assert(properties["file-local-options/auto-window-resize"] == false or input.params.graph == source_graph)
                assert(value[2] == output and value[3] == user_filter, "Other filters changed")
                updates = updates + 1
            end
            properties[name] = value
            return true
        end,
        add_hook = function(name, _, callback) hooks[name] = callback end,
        register_event = function(name, callback) events[name] = callback end,
        observe_property = function(name, _, callback) observers[name] = callback end,
        register_script_message = function() end,
        add_timeout = function(_, callback, disabled)
            timer = {
                active = not disabled,
                kill = function(self) self.active = false end,
                resume = function(self) self.active = true end,
                fire = function(self)
                    assert(self.active, "Resize was not scheduled")
                    self.active = false
                    callback()
                end,
            }
            return timer
        end,
    }
    dofile(assert(os.getenv("OPEN_SVPFLOW_TEST_COMPAT")))
    assert(observers["video-target-params/w"], "Rendered video width is not tracked")
    assert(observers["video-target-params/h"], "Rendered video height is not tracked")
    hooks.on_preloaded()
    events["playback-restart"]()
    timer:fire()
    assert(input.params.graph:find("min(2528,1422*iw/ih)", 1, true))
    assert(input.params.graph:find("min(1422,2528*ih/iw)", 1, true))
    properties["video-target-params"] = {w = 1700, h = 956}
    properties["osd-dimensions"] = {w = 1700, h = 1024}
    observers["video-target-params/w"]()
    properties["video-target-params"] = {w = 1280, h = 720}
    properties["osd-dimensions"] = {w = 1280, h = 800}
    observers["video-target-params/h"]()
    assert(updates == 1, "Filter rebuilt during an interactive resize")
    timer:fire()
    assert(updates == 2 and input.params.graph:find("min(1280,720*iw/ih)", 1, true))
    events["video-reconfig"]()
    assert(updates == 2, "Filter rebuilt again during output reconfiguration")
    properties["video-target-params"] = {w = 1280, h = 719}
    observers["video-target-params/h"]()
    timer:fire()
    assert(updates == 2, "A rounding change fed back into an unchanged viewport")
    properties["osd-dimensions"] = {w = 2048, h = 1400, ml = 64, mr = 64, mt = 160, mb = 160}
    observers["osd-dimensions"]()
    timer:fire()
    assert(input.params.graph:find("min(1920,1080*iw/ih)", 1, true), "A stale renderer size overrode the current video rectangle")
    assert(input.params.graph:find("min(1080,1920*ih/iw)", 1, true))
    user_filter.enabled = true
    events["video-reconfig"]()
    assert(input.params.graph == source_graph, "A downstream filter received unexpectedly scaled input")
    user_filter.enabled = false
    properties["options/video-unscaled"] = "downscale-big"
    properties["video-target-params"] = {w = 1920, h = 1080}
    properties["osd-dimensions"] = {w = 2400, h = 1350}
    observers["osd-dimensions"]()
    timer:fire()
    assert(input.params.graph:find("min(2400,1350*iw/ih)", 1, true), "An unscaled renderer could not regain resolution")
    properties["options/video-unscaled"] = "no"
    properties["video-target-params"] = {w = 5120, h = 2880}
    properties["osd-dimensions"] = {w = 5120, h = 2880}
    observers["video-target-params/w"]()
    timer:fire()
    assert(input.params.graph == source_graph, "Larger viewport upscaled the source")
    observers["video-target-params/h"]()
    local unchanged = updates
    properties["video-dec-params"] = nil
    events["video-reconfig"]()
    assert(updates == unchanged, "A reconfiguration without video changed the filters")
    events["end-file"]()
    assert(not timer.active, "Resize survived end of file")
end

local ok, error_message = pcall(verify)
if ok then
    print("PASS: viewport geometry and resize handling")
else
    print(error_message)
end
host.commandv("quit", ok and 0 or 1)

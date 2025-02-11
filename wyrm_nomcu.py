#!/usr/bin/env python3

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput

from litex_boards.platforms import colorlight_5a_75b

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.interconnect.csr import *
from litex.soc.cores.gpio import GPIOOut

from litedram.modules import M12L64322A
from litedram.phy import GENSDRPHY, HalfRateGENSDRPHY

from liteeth.phy.ecp5rgmii import LiteEthPHYRGMII
from liteeth.core import LiteEthUDPIPCore
from liteeth.frontend.stream import LiteEthUDPStreamer
from liteeth.frontend.etherbone import LiteEthEtherbone
from litex.build.generic_platform import *

from litescope import LiteScopeAnalyzer

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, use_internal_osc=False, with_usb_pll=False, with_rst=True, sdram_rate="1:1"):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()
        self.cd_sys_div2 = ClockDomain()
        if sdram_rate == "1:2":
            self.cd_sys2x    = ClockDomain()
            self.cd_sys2x_ps = ClockDomain()
        else:
            self.cd_sys_ps = ClockDomain()

        # # #

        # Clk / Rst
        if not use_internal_osc:
            clk = platform.request("clk25")
            clk_freq = 25e6
        else:
            clk = Signal()
            div = 5
            self.specials += Instance("OSCG",
                                p_DIV = div,
                                o_OSC = clk)
            clk_freq = 310e6/div

        self.rst_n = rst_n = 1 if not with_rst else platform.request("user_btn_n", 0)

        # PLL
        self.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(~rst_n | self.rst)
        pll.register_clkin(clk, clk_freq)
        pll.create_clkout(self.cd_sys,    sys_clk_freq)
        if sdram_rate == "1:2":
            pll.create_clkout(self.cd_sys2x,    2*sys_clk_freq)
            pll.create_clkout(self.cd_sys2x_ps, 2*sys_clk_freq, phase=180) # Idealy 90° but needs to be increased.
        else:
           pll.create_clkout(self.cd_sys_ps, sys_clk_freq, phase=180) # Idealy 90° but needs to be increased.

        # display clock
        pll.create_clkout(self.cd_sys_div2, sys_clk_freq/2)

        # USB PLL
        if with_usb_pll:
            self.usb_pll = usb_pll = ECP5PLL()
            self.comb += usb_pll.reset.eq(~rst_n | self.rst)
            usb_pll.register_clkin(clk, clk_freq)
            self.cd_usb_12 = ClockDomain()
            self.cd_usb_48 = ClockDomain()
            usb_pll.create_clkout(self.cd_usb_12, 12e6, margin=0)
            usb_pll.create_clkout(self.cd_usb_48, 48e6, margin=0)

        # SDRAM clock
        sdram_clk = ClockSignal("sys2x_ps" if sdram_rate == "1:2" else "sys_ps")
        self.specials += DDROutput(1, 0, platform.request("sdram_clock"), sdram_clk)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCMini):
    def __init__(self, revision, sys_clk_freq=50e6, toolchain="trellis",
        eth_ip           = "192.168.10.30",
        eth_phy          = 0,
        use_internal_osc = False,
        sdram_rate       = "1:1",
        with_spi_flash   = False,
        rom              = None,
        rgb_order        = 'rgb',
        **kwargs):
        platform = colorlight_5a_75b.Platform(revision=revision, toolchain=toolchain)

        # Extend Platform --------------------------------------------------------------------------
        platform.add_source("ledpanel.v")
        platform.add_source("udp_panel_writer.v")

        # A note about "shared_output":
        # _connectors_v8_0 in litex-boards/litex_boards/platforms/colorlight_5a_75b
        # has the same values for columns 7-14. That's because the FPGA balls
        # corresponding to the a/b/c/d/e/clk/stb/oe pins on all 8 connectors on
        # the board. (The same is true for other versions).
        #
        # These are kept separate with "number" = 0 so that platform.request will
        # not return the same ball multiple times.
        platform.add_extension([
            ("shared_output", 0,
                Subsignal("panel_e", Pins("j1:7")),
                Subsignal("panel_a", Pins("j1:8")),
                Subsignal("panel_b", Pins("j1:9")),
                Subsignal("panel_c", Pins("j1:10")),
                Subsignal("panel_d", Pins("j1:11")),
                Subsignal("panel_clk", Pins("j1:12")),
                Subsignal("panel_stb", Pins("j1:13")),
                Subsignal("panel_oe", Pins("j1:14")),
                IOStandard("LVCMOS33"))])

        assert len(rgb_order) == 3
        assert 'r' in rgb_order
        assert 'g' in rgb_order
        assert 'b' in rgb_order

        r_offset = rgb_order.index('r')
        g_offset = rgb_order.index('g')
        b_offset = rgb_order.index('b')

        # Unlike the shared_output, each connector listed here has a unique ball
        # it's tied to; we need a resource for each of the 8 connectors.
        for connector in range(1,9):
            platform.add_extension([
                ("rgb_output", connector,
                    Subsignal("panel_r0", Pins(f"j{connector}:{r_offset}")),
                    Subsignal("panel_g0", Pins(f"j{connector}:{g_offset}")),
                    Subsignal("panel_b0", Pins(f"j{connector}:{b_offset}")),
                    Subsignal("panel_r1", Pins(f"j{connector}:{r_offset+4}")),
                    Subsignal("panel_g1", Pins(f"j{connector}:{g_offset+4}")),
                    Subsignal("panel_b1", Pins(f"j{connector}:{b_offset+4}")),
                    IOStandard("LVCMOS33"))])

        # LED Panel --------------------------------------------------------------------------------
        self.ctrl_signals = ctrl_signals = Record([
            ("en", 8),
            ("addr", 16),
            ("wdat", 24),
        ])

        s_shared_en = self.ctrl_signals.en
        s_shared_addr = self.ctrl_signals.addr
        s_shared_wdat = self.ctrl_signals.wdat

        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 1),
            select_line=0,
            shared_output=platform.request('shared_output'))
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 2),
            select_line=1)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 3),
            select_line=2)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 4),
            select_line=3)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 5),
            select_line=4)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 6),
            select_line=5)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 7),
            select_line=6)
        self.add_ledpanel(
            rgb_output=platform.request("rgb_output", 8),
            select_line=7)

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, int(sys_clk_freq),
            use_internal_osc = use_internal_osc,
            with_usb_pll     = False,
            with_rst         = False,
            sdram_rate       = sdram_rate
        )

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self,
            platform,
            clk_freq=int(sys_clk_freq),
        )

        # LiteEth UDP/IP ---------------------------------------------------------------------------
        self.ethphy = LiteEthPHYRGMII(
            clock_pads = self.platform.request("eth_clocks", eth_phy),
            pads       = self.platform.request("eth", eth_phy),
            tx_delay   = 0e-9,
        )
        self.submodules.ethcore = udp_core = LiteEthUDPIPCore(
            self.ethphy,
            mac_address = 0x726b895bc2e2,
            ip_address  = eth_ip,
            clk_freq    = int(sys_clk_freq),
            dw          = 32,
            with_ip_broadcast = True,
            with_sys_datapath = True,
            endianness  = "big",
            #interface   = "crossbar",
        )

        # Instantiate a dummy port to make the UDP IP Core and ARP circuitry work
        udp_port = udp_core.udp.crossbar.get_port(2025, dw=32, cd="sys")
        self.comb += udp_port.source.ready.eq(1)

        udp_rx = udp_core.udp.rx.source

        udp_panel_writer = Instance("udp_panel_writer",
            Instance.Input('clk', ClockSignal()),
            Instance.Input('reset'),
            Instance.Input('udp_source_valid'),
            Instance.Input('udp_source_last'),
            Instance.Output('udp_source_ready'),
            Instance.Input('udp_source_src_port', Signal(16)),
            Instance.Input('udp_source_dst_port', Signal(16)),
            Instance.Input('udp_source_ip_address', Signal(32)),
            Instance.Input('udp_source_length', Signal(16)),
            Instance.Input('udp_source_data', Signal(32)),
            Instance.Input('udp_source_error', Signal(4)),
            Instance.Output('ctrl_en', s_shared_en),
            Instance.Output('ctrl_addr', s_shared_addr),
            Instance.Output('ctrl_wdat', s_shared_wdat),
            Instance.Output('led_reg'),
        )

        self.specials += udp_panel_writer

        self.comb += udp_panel_writer.get_io('reset').eq(0)

        self.sync += [
            udp_panel_writer.get_io('udp_source_valid').eq(udp_rx.valid),
            udp_panel_writer.get_io('udp_source_last').eq(udp_rx.last),
            udp_rx.ready.eq(udp_panel_writer.get_io('udp_source_ready')),
            udp_panel_writer.get_io('udp_source_dst_port').eq(udp_rx.param.dst_port),
            udp_panel_writer.get_io('udp_source_src_port').eq(15),
            udp_panel_writer.get_io('udp_source_data').eq(udp_rx.payload.data),
            udp_panel_writer.get_io('udp_source_error').eq(udp_rx.payload.error),
        ]

        s_test_port = Signal()
        self.comb += s_test_port.eq((udp_core.udp.rx.source.param.dst_port[15] == 1) & udp_core.udp.rx.source.valid)
        led = platform.request("user_led_n", 0)
        self.comb += led.eq(udp_panel_writer.get_io('led_reg'))

        # SPI Flash --------------------------------------------------------------------------------
        if with_spi_flash:
            from litespi.modules import W25Q32JV as SpiFlashModule

            from litespi.opcodes import SpiNorFlashOpCodes
            self.mem_map["spiflash"] = 0x20000000
            self.add_spi_flash(mode="1x", module=SpiFlashModule(SpiNorFlashOpCodes.READ_1_1_1), with_master=False)

    def add_ledpanel(self, rgb_output: Record, select_line: int, shared_output: Record|None = None) -> None:
        ledpanel = Instance("ledpanel",
            Instance.Input('ctrl_clk', ClockSignal()),
            Instance.Input('ctrl_en'),
            Instance.Input('ctrl_addr', Signal(16)),
            Instance.Input('ctrl_wdat', Signal(24)),
            Instance.Input('display_clock', ClockSignal("sys")),
            Instance.Output('panel_r0'),
            Instance.Output('panel_g0'),
            Instance.Output('panel_b0'),
            Instance.Output('panel_r1'),
            Instance.Output('panel_g1'),
            Instance.Output('panel_b1'),
            Instance.Output('panel_a'),
            Instance.Output('panel_b'),
            Instance.Output('panel_c'),
            Instance.Output('panel_d'),
            Instance.Output('panel_e'),
            Instance.Output('panel_clk'),
            Instance.Output('panel_stb'),
            Instance.Output('panel_oe'),
        )

        self.specials += ledpanel

        self.comb += [
            rgb_output.panel_r0.eq(ledpanel.get_io('panel_r0')),
            rgb_output.panel_g0.eq(ledpanel.get_io('panel_g0')),
            rgb_output.panel_b0.eq(ledpanel.get_io('panel_b0')),
            rgb_output.panel_r1.eq(ledpanel.get_io('panel_r1')),
            rgb_output.panel_g1.eq(ledpanel.get_io('panel_g1')),
            rgb_output.panel_b1.eq(ledpanel.get_io('panel_b1')),
        ]

        self.comb += [
            ledpanel.get_io('ctrl_en').eq(self.ctrl_signals.en[select_line]),
            ledpanel.get_io('ctrl_addr').eq(self.ctrl_signals.addr),
            ledpanel.get_io('ctrl_wdat').eq(self.ctrl_signals.wdat),
        ]

        if shared_output:
            self.comb += [
                shared_output.panel_a.eq(ledpanel.get_io('panel_a')),
                shared_output.panel_b.eq(ledpanel.get_io('panel_b')),
                shared_output.panel_c.eq(ledpanel.get_io('panel_c')),
                shared_output.panel_d.eq(ledpanel.get_io('panel_d')),
                shared_output.panel_e.eq(ledpanel.get_io('panel_e')),
                shared_output.panel_clk.eq(ledpanel.get_io('panel_clk')),
                shared_output.panel_stb.eq(ledpanel.get_io('panel_stb')),
                shared_output.panel_oe.eq(ledpanel.get_io('panel_oe')),
            ]

# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=colorlight_5a_75b.Platform, description="LiteX SoC on Colorlight 5A-75X.")
    parser.add_target_argument("--revision",          default="8.2",             help="Board revision (6.0, 6.1, 7.0, 8.0, or 8.2).")
    parser.add_target_argument("--sys-clk-freq",      default=50e6, type=float,  help="System clock frequency.")
    parser.add_target_argument("--eth-ip",            default="192.168.10.30",   help="Ethernet/Etherbone IP address.")
    parser.add_target_argument("--eth-phy",           default=0, type=int,       help="Ethernet PHY (0 or 1).")
    parser.add_target_argument("--sdram-rate",        default="1:1",             help="SDRAM Rate (1:1 Full Rate or 1:2 Half Rate).")
    parser.add_target_argument("--with-spi-flash",    action="store_true",       help="Add SPI flash support to the SoC")
    parser.add_target_argument("--flash",             action="store_true",       help="Flash the code to the target FPGA")
    args = parser.parse_args()

    soc = BaseSoC(revision=args.revision,
        sys_clk_freq     = args.sys_clk_freq,
        toolchain        = args.toolchain,
        eth_ip           = args.eth_ip,
        eth_phy          = args.eth_phy,
        sdram_rate       = args.sdram_rate,
        with_spi_flash   = args.with_spi_flash
    )
    builder = Builder(soc, **parser.builder_argdict)

    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram", ext=".svf")) # FIXME

    if args.flash:
        prog = soc.platform.create_programmer()
        os.system("cp bit_to_flash.py build/colorlight_5a_75b/gateware/")
        os.system("cd build/colorlight_5a_75b/gateware/ && ./bit_to_flash.py colorlight_5a_75b.bit wyrm_flash.svf")
        prog.load_bitstream("build/colorlight_5a_75b/gateware/wyrm_flash.svf")

if __name__ == "__main__":
    main()

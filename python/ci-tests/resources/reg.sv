module register #(
  parameter int W = 32
) (
  input  logic           clk,
  input  logic           rst,       // 高有效复位
  input  logic [W-1:0]   reg_in,

  output logic [W-1:0]   reg_out
);

  logic rst_n;
  assign rst_n = ~rst;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      reg_out <= '0;
    end else begin
      reg_out      <= reg_in;
    end
  end


endmodule
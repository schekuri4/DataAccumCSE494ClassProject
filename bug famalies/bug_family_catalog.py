#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class BugFamily:
    family_id: str
    bug_type: str
    category: str
    match_targets: list[str]
    mutation_strategy: str
    repair_expectation: str
    validation_signal: str
    tags: list[str]


CategorySpec = tuple[str, list[str], list[tuple[str, list[str], str, str]]]


CATEGORY_SPECS: list[CategorySpec] = [
    (
        "include_headers",
        ["include", "header", "dependency"],
        [
            ("missing_adf_header", ["#include <adf.h>", "#include \"adf.h\""], "Remove or rename the ADF header include.", "Restore the correct adf.h include."),
            ("missing_aie_api_header", ["#include <aie_api/aie.hpp>", "#include <aie_api/*.hpp>"], "Rename an AIE API include to a non-existent path.", "Restore the correct AIE API header path."),
            ("missing_kernel_header", ["project-local kernel header includes"], "Rename a local kernel header include.", "Restore the correct local header name."),
            ("angle_quote_swap_local", ["#include \"local.h\""], "Change a local quoted include into an angle include.", "Use the local quoted include form."),
            ("wrong_relative_include_depth", ["../", "../../", "local includes"], "Add or remove one relative directory component.", "Restore the include path relative to the file."),
            ("duplicate_guard_macro_collision", ["#ifndef", "#define include guards"], "Change an include guard macro to collide with another header.", "Restore a unique include guard macro."),
            ("missing_stdint_for_fixed_width", ["int16_t", "uint32_t", "stdint.h", "cstdint"], "Remove the fixed-width integer header.", "Restore the fixed-width integer include."),
            ("wrong_case_header_path", ["project-local includes"], "Change filename case in an include path.", "Restore exact case-sensitive header spelling."),
            ("wrong_library_namespace_header", ["aie_api", "adf", "aie"] , "Swap an AIE API header with a similarly named wrong library header.", "Restore the matching library header."),
            ("commented_required_include", ["required includes"], "Comment out a required include line.", "Uncomment the required include."),
        ],
    ),
    (
        "graph_kernel_binding",
        ["graph", "kernel", "binding"],
        [
            ("unknown_kernel_create_symbol", ["kernel::create(foo)"], "Append a suffix to the kernel function used by kernel::create.", "Restore the real kernel function symbol."),
            ("wrong_kernel_create_overload", ["kernel::create"], "Wrap the kernel function in an invalid cast or call expression.", "Use the supported kernel::create form."),
            ("missing_kernel_declaration", ["kernel prototypes", "extern declarations"], "Remove the declaration visible to the graph header.", "Restore a visible kernel prototype."),
            ("kernel_variable_type_mismatch", ["adf::kernel k"], "Change an adf::kernel member to an incompatible graph object type.", "Restore the adf::kernel member type."),
            ("kernel_array_index_oob", ["kernel arrays"], "Use an out-of-range constant index when setting source or runtime.", "Use a valid kernel array index."),
            ("kernel_name_namespace_removed", ["ns::kernel_fn"], "Remove the namespace qualifier from a kernel function reference.", "Restore the required namespace qualifier."),
            ("kernel_name_namespace_added", ["kernel_fn"], "Add a bogus namespace qualifier to a kernel function reference.", "Use the unqualified or correct qualified symbol."),
            ("wrong_kernel_template_instantiation", ["templated kernel functions"], "Change a kernel template argument value or type.", "Restore the valid kernel template instantiation."),
            ("kernel_member_shadowed", ["adf::kernel members"], "Introduce a local symbol that shadows the graph kernel member.", "Remove the shadow or reference the member explicitly."),
            ("missing_kernel_source_assignment", ["source(k) ="], "Delete the source assignment for a created kernel.", "Restore source(k) assignment to the kernel file."),
        ],
    ),
    (
        "graph_source_paths",
        ["graph", "source", "file path"],
        [
            ("wrong_adf_source_filename", ["adf::source(k)", "source(k)"], "Change the kernel source filename to a missing file.", "Restore the correct kernel source path."),
            ("wrong_source_extension_cc_cpp", [".cc", ".cpp"], "Swap .cc and .cpp in a source assignment.", "Use the actual source filename extension."),
            ("source_path_missing_subdir", ["kernels/foo.cc"], "Remove the kernels subdirectory from the source path.", "Restore the relative subdirectory."),
            ("source_path_extra_subdir", ["foo.cc"], "Add a non-existent subdirectory to the source path.", "Restore the actual relative path."),
            ("source_string_unterminated", ["source(k) = \"...\""], "Remove the closing quote from a source path string.", "Restore the complete string literal."),
            ("source_assignment_wrong_kernel", ["multiple kernels"], "Assign one kernel member the source file for another kernel.", "Point each kernel to its own source file."),
            ("source_path_backslash_escape", ["Windows-style paths"], "Use unescaped backslashes in a source string.", "Use forward slashes or escaped backslashes."),
            ("source_path_absolute_windows", ["source(k) path"], "Replace a portable relative path with an invalid Windows absolute path.", "Restore a portable project-relative path."),
            ("source_missing_semicolon", ["source(k) = \"file.cc\";"], "Remove the semicolon after a source assignment.", "Restore the semicolon."),
            ("source_api_misspelled", ["adf::source", "source"], "Misspell the source API name.", "Restore adf::source/source spelling."),
        ],
    ),
    (
        "graph_connections",
        ["graph", "connect", "ports"],
        [
            ("misspelled_connect_api", ["connect<", "adf::connect<"], "Misspell connect as conect.", "Restore connect spelling."),
            ("wrong_connect_template_window_stream", ["connect<window<N>>", "connect<stream>"], "Swap the connect template kind between window and stream.", "Restore the template kind matching the endpoints."),
            ("wrong_connect_window_size", ["connect<window<N>>"], "Change the window byte count template value.", "Restore the window size expected by the kernel interface."),
            ("missing_connect_template_arg", ["connect<...>"], "Remove the connect template argument list.", "Restore the required connect template argument."),
            ("connect_endpoint_typo", ["port names", ".in[0]", ".out[0]"], "Append a suffix to one endpoint member name.", "Restore the real endpoint name."),
            ("connect_input_to_input", ["input ports"], "Connect two input endpoints together.", "Connect producer output to consumer input."),
            ("connect_output_to_output", ["output ports"], "Connect two output endpoints together.", "Connect producer output to consumer input."),
            ("connect_index_out_of_range", ["in[N]", "out[N]"], "Increase an endpoint array index beyond the declared size.", "Use a declared endpoint index."),
            ("connect_missing_semicolon", ["connect statements"], "Remove the semicolon after a connect call.", "Restore the semicolon."),
            ("connect_wrong_graph_object", ["multiple graph objects"], "Use an endpoint from an unrelated graph object.", "Use endpoints from the intended graph objects."),
        ],
    ),
    (
        "plio_gmio_ports",
        ["PLIO", "GMIO", "ports"],
        [
            ("plio_input_replaced_with_output", ["input_plio"], "Change input_plio to output_plio.", "Restore input_plio for input-side ports."),
            ("plio_output_replaced_with_input", ["output_plio"], "Change output_plio to input_plio.", "Restore output_plio for output-side ports."),
            ("gmio_input_replaced_with_output", ["input_gmio"], "Change input_gmio to output_gmio.", "Restore input_gmio for input-side GMIO."),
            ("gmio_output_replaced_with_input", ["output_gmio"], "Change output_gmio to input_gmio.", "Restore output_gmio for output-side GMIO."),
            ("plio_width_invalid", ["plio_32_bits", "plio_64_bits", "plio_128_bits"], "Change the PLIO width enum to an invalid value.", "Restore a supported PLIO width enum."),
            ("gmio_depth_invalid", ["GMIO depth arguments"], "Change a GMIO depth or burst argument to an invalid literal.", "Restore the supported GMIO argument."),
            ("plio_filename_missing", ["plio::create file names"], "Change the PLIO data filename to a missing path.", "Restore the expected data filename."),
            ("plio_factory_misspelled", ["plio::create"], "Misspell the PLIO create factory.", "Restore plio::create spelling."),
            ("gmio_factory_misspelled", ["gmio::create"], "Misspell the GMIO create factory.", "Restore gmio::create spelling."),
            ("port_member_type_mismatch", ["port<input>", "port<output>"], "Swap port direction template types.", "Restore port direction to match dataflow."),
        ],
    ),
    (
        "stream_interfaces",
        ["stream", "kernel signature"],
        [
            ("input_stream_replaced_with_output_stream", ["input_stream<T>"], "Change input_stream to output_stream in a kernel parameter.", "Restore input_stream for consumed streams."),
            ("output_stream_replaced_with_input_stream", ["output_stream<T>"], "Change output_stream to input_stream in a kernel parameter.", "Restore output_stream for produced streams."),
            ("stream_element_type_changed", ["input_stream<int16>", "output_stream<cint16>"], "Change the stream element type.", "Restore the element type matching graph connections."),
            ("readincr_on_output_stream", ["readincr", "output_stream"], "Use readincr on an output stream parameter.", "Use writeincr for output streams."),
            ("writeincr_on_input_stream", ["writeincr", "input_stream"], "Use writeincr on an input stream parameter.", "Use readincr for input streams."),
            ("readincr_v_lane_mismatch", ["readincr_v<N>"], "Change the readincr_v vector lane template value.", "Restore the vector lane count expected by the variable type."),
            ("writeincr_v_lane_mismatch", ["writeincr_v<N>"], "Change the writeincr_v vector lane template value.", "Restore the vector lane count expected by the variable type."),
            ("stream_parameter_pointer_removed", ["input_stream<T>*"], "Remove the pointer marker from a stream parameter.", "Restore pointer/reference syntax required by AIE stream APIs."),
            ("stream_parameter_const_added", ["input_stream<T>*", "output_stream<T>*"], "Add const to a stream pointer that APIs mutate.", "Remove invalid const qualifier."),
            ("stream_api_namespace_removed", ["adf::input_stream", "adf::output_stream"], "Remove the adf namespace qualifier where required.", "Restore the stream type namespace."),
        ],
    ),
    (
        "window_buffer_interfaces",
        ["window", "buffer", "kernel signature"],
        [
            ("input_window_replaced_with_output_window", ["input_window<T>"], "Change input_window to output_window.", "Restore input_window for input buffers."),
            ("output_window_replaced_with_input_window", ["output_window<T>"], "Change output_window to input_window.", "Restore output_window for output buffers."),
            ("input_buffer_replaced_with_output_buffer", ["input_buffer<T>"], "Change input_buffer to output_buffer.", "Restore input_buffer for consumed buffers."),
            ("output_buffer_replaced_with_input_buffer", ["output_buffer<T>"], "Change output_buffer to input_buffer.", "Restore output_buffer for produced buffers."),
            ("window_element_type_changed", ["input_window<int16>", "output_window<cint16>"], "Change the window element type.", "Restore the element type matching the graph connection."),
            ("buffer_dimension_template_changed", ["input_buffer<T, adf::extents<N>>"], "Change a buffer extent template value.", "Restore the buffer extent expected by the kernel."),
            ("window_read_api_on_output", ["window_readincr", "output_window"], "Use a window read API on an output window.", "Use the correct output write API."),
            ("window_write_api_on_input", ["window_writeincr", "input_window"], "Use a window write API on an input window.", "Use the correct input read API."),
            ("buffer_begin_vector_lane_mismatch", ["begin_vector<N>", "begin_restrict_vector<N>"], "Change the begin_vector lane count.", "Restore the vector lane count matching the declared vector type."),
            ("buffer_margin_removed", ["margin<N>", "window margins"], "Remove or shrink the declared margin.", "Restore the margin required by the kernel access pattern."),
        ],
    ),
    (
        "runtime_location_constraints",
        ["runtime", "location", "constraints"],
        [
            ("runtime_api_misspelled", ["runtime<ratio>"], "Misspell runtime as runtim.", "Restore runtime API spelling."),
            ("runtime_ratio_template_wrong", ["runtime<ratio>"], "Change runtime<ratio> to an unsupported template argument.", "Restore runtime<ratio>."),
            ("runtime_ratio_value_out_of_range", ["runtime<ratio>(k) = value"], "Set the ratio to an invalid value.", "Restore a valid ratio value."),
            ("location_api_misspelled", ["location<kernel>"], "Misspell the location API.", "Restore location API spelling."),
            ("wrong_tile_coordinate_type", ["tile(x,y)"], "Replace a tile coordinate with a non-integer token.", "Restore integer tile coordinates."),
            ("location_template_wrong_kind", ["location<kernel>", "location<buffer>"], "Swap the location template kind.", "Restore the location kind matching the object."),
            ("constraint_wrong_kernel_symbol", ["runtime(k)", "location(k)"], "Apply a constraint to a missing or wrong kernel symbol.", "Use the declared kernel symbol."),
            ("fifo_depth_invalid", ["fifo_depth", "dimensions"], "Set FIFO depth to an invalid literal.", "Restore a supported FIFO depth."),
            ("repetition_count_invalid", ["repetition_count"], "Set repetition_count to an invalid expression.", "Restore a valid repetition count."),
            ("graph_constraint_missing_semicolon", ["constraint statements"], "Remove the semicolon after a constraint.", "Restore the semicolon."),
        ],
    ),
    (
        "vector_load_store",
        ["vector", "load", "store"],
        [
            ("load_v_lane_mismatch", ["aie::load_v<N>"], "Change the load_v lane template value.", "Restore the lane count matching the vector type."),
            ("store_v_lane_mismatch", ["aie::store_v<N>"], "Change the store_v lane template value.", "Restore the lane count matching the vector type."),
            ("load_v_type_mismatch", ["aie::vector<T,N>", "load_v<N>"], "Change the destination vector element type.", "Restore the vector element type matching memory."),
            ("store_v_pointer_type_mismatch", ["store_v pointer argument"], "Change the pointer type used by store_v.", "Restore pointer type compatible with the vector."),
            ("begin_vector_removed_template", ["begin_vector<N>"], "Remove the template argument from begin_vector.", "Restore the required vector lane template."),
            ("begin_restrict_vector_misspelled", ["begin_restrict_vector"], "Misspell begin_restrict_vector.", "Restore begin_restrict_vector spelling."),
            ("vector_decl_lane_mismatch", ["aie::vector<T,N>"], "Change N in a vector declaration without changing operations.", "Restore consistent vector lane count."),
            ("vector_extract_index_oob", ["extract<N>", "get<N>"], "Increase a vector lane extraction index beyond the vector size.", "Use a valid lane index."),
            ("vector_insert_index_oob", ["insert<N>"], "Increase a vector lane insertion index beyond the vector size.", "Use a valid lane index."),
            ("unaligned_vector_pointer_cast", ["reinterpret_cast", "vector loads"], "Cast a pointer to an incompatible vector element type.", "Use the pointer type matching the data."),
        ],
    ),
    (
        "vector_lane_widths",
        ["vector", "lanes", "width"],
        [
            ("int16_vector_16_to_8", ["aie::vector<int16,16>"], "Change a 16-lane int16 vector to 8 lanes.", "Restore 16 lanes."),
            ("int16_vector_8_to_16", ["aie::vector<int16,8>"], "Change an 8-lane int16 vector to 16 lanes.", "Restore 8 lanes."),
            ("int32_vector_8_to_4", ["aie::vector<int32,8>"], "Change an 8-lane int32 vector to 4 lanes.", "Restore 8 lanes."),
            ("int32_vector_4_to_8", ["aie::vector<int32,4>"], "Change a 4-lane int32 vector to 8 lanes.", "Restore 4 lanes."),
            ("cint16_vector_8_to_4", ["aie::vector<cint16,8>"], "Change an 8-lane cint16 vector to 4 lanes.", "Restore 8 lanes."),
            ("cint32_vector_4_to_2", ["aie::vector<cint32,4>"], "Change a 4-lane cint32 vector to 2 lanes.", "Restore 4 lanes."),
            ("float_vector_8_to_4", ["aie::vector<float,8>"], "Change an 8-lane float vector to 4 lanes.", "Restore 8 lanes."),
            ("bfloat16_vector_16_to_8", ["aie::vector<bfloat16,16>"], "Change a 16-lane bfloat16 vector to 8 lanes.", "Restore 16 lanes."),
            ("vector_lane_non_power_of_two", ["aie::vector<T,N>"], "Change N to a non-supported lane count like 7.", "Restore a supported lane count."),
            ("vector_lane_zero", ["aie::vector<T,N>"], "Change N to 0.", "Restore a positive supported lane count."),
        ],
    ),
    (
        "accumulators",
        ["accumulator", "accum", "mac", "mul"],
        [
            ("acc80_replaced_with_acc48", ["aie::accum<acc80,N>"], "Change acc80 to acc48.", "Restore acc80 for the operation width."),
            ("acc48_replaced_with_acc80", ["aie::accum<acc48,N>"], "Change acc48 to acc80.", "Restore acc48 where expected."),
            ("acc64_replaced_with_acc32", ["aie::accum<acc64,N>"], "Change acc64 to acc32.", "Restore acc64."),
            ("accfloat_replaced_with_acc80", ["aie::accum<accfloat,N>"], "Change floating accumulator type to integer accumulator type.", "Restore accfloat."),
            ("accum_lane_mismatch", ["aie::accum<accXX,N>"], "Change the accumulator lane count.", "Restore lane count matching vectors."),
            ("zeros_initializer_wrong_type", ["aie::zeros<accum_type,N>()"], "Change zeros initializer template type.", "Restore the accumulator type in zeros."),
            ("broadcast_initializer_wrong_lane", ["aie::broadcast<T,N>"], "Change broadcast lane count for accumulator/vector init.", "Restore lane count matching destination."),
            ("to_vector_shift_invalid", ["to_vector<T>(shift)"], "Change the shift argument to an invalid literal.", "Restore a valid conversion shift."),
            ("accum_add_vector_type_mismatch", ["acc + vector", "mac"], "Change a vector operand element type used with an accumulator.", "Restore compatible vector type."),
            ("accumulator_decl_missing_aie_namespace", ["aie::accum"], "Remove the aie namespace from an accumulator declaration.", "Restore aie::accum."),
        ],
    ),
    (
        "arithmetic_intrinsics",
        ["intrinsics", "mul", "mac"],
        [
            ("mul_intrinsic_misspelled", ["aie::mul"], "Misspell aie::mul.", "Restore aie::mul spelling."),
            ("mac_intrinsic_misspelled", ["aie::mac"], "Misspell aie::mac.", "Restore aie::mac spelling."),
            ("sliding_mul_template_mismatch", ["aie::sliding_mul"], "Change a sliding_mul template argument.", "Restore valid sliding_mul template arguments."),
            ("sliding_mac_template_mismatch", ["aie::sliding_mac"], "Change a sliding_mac template argument.", "Restore valid sliding_mac template arguments."),
            ("mul_operand_order_invalid", ["aie::mul operands"], "Swap operands into an unsupported overload order.", "Restore operand order accepted by the intrinsic."),
            ("mac_missing_accumulator_operand", ["aie::mac"], "Remove the accumulator operand from mac.", "Restore the accumulator argument."),
            ("add_intrinsic_type_mismatch", ["aie::add"], "Change one operand vector type.", "Restore matching operand types."),
            ("sub_intrinsic_type_mismatch", ["aie::sub"], "Change one operand vector lane count.", "Restore matching lane counts."),
            ("neg_intrinsic_wrong_type", ["aie::neg"], "Apply neg to an unsupported accumulator/vector type.", "Restore a supported input type."),
            ("shift_intrinsic_invalid_amount", ["aie::srs", "aie::ups"], "Change shift amount to an invalid literal.", "Restore a valid shift amount."),
        ],
    ),
    (
        "complex_datatypes",
        ["complex", "cint", "cfloat"],
        [
            ("cint16_replaced_with_int16", ["cint16"], "Change complex int16 type to scalar int16.", "Restore cint16."),
            ("int16_replaced_with_cint16", ["int16"], "Change scalar int16 type to complex cint16.", "Restore int16."),
            ("cint32_replaced_with_int32", ["cint32"], "Change complex int32 type to scalar int32.", "Restore cint32."),
            ("int32_replaced_with_cint32", ["int32"], "Change scalar int32 type to complex cint32.", "Restore int32."),
            ("cfloat_replaced_with_float", ["cfloat"], "Change complex float type to scalar float.", "Restore cfloat."),
            ("float_replaced_with_cfloat", ["float"], "Change scalar float type to complex cfloat.", "Restore float."),
            ("complex_constructor_missing_imag", ["cint16(...)", "cfloat(...)"], "Remove the imaginary component argument.", "Restore both real and imaginary components."),
            ("real_imag_member_typo", [".real", ".imag"], "Misspell real or imag member access.", "Restore the member name."),
            ("complex_vector_lane_mismatch", ["aie::vector<cint16,N>", "aie::vector<cfloat,N>"], "Change lane count for complex vector operations.", "Restore consistent complex vector lane count."),
            ("complex_mul_accum_type_mismatch", ["complex mul/mac"], "Change accumulator type used for complex multiplication.", "Restore accumulator type compatible with complex operands."),
        ],
    ),
    (
        "cascade_streams",
        ["cascade", "stream", "accumulator"],
        [
            ("input_cascade_replaced_with_output_cascade", ["input_cascade"], "Change input_cascade to output_cascade.", "Restore input_cascade for consumed cascade data."),
            ("output_cascade_replaced_with_input_cascade", ["output_cascade"], "Change output_cascade to input_cascade.", "Restore output_cascade for produced cascade data."),
            ("cascade_accumulator_type_changed", ["input_cascade<accXX>", "output_cascade<accXX>"], "Change cascade accumulator template type.", "Restore matching accumulator type."),
            ("readincr_v_on_cascade", ["readincr_v", "cascade"], "Use a vector stream read API on a cascade stream.", "Use the cascade read API."),
            ("writeincr_v_on_cascade", ["writeincr_v", "cascade"], "Use a vector stream write API on a cascade stream.", "Use the cascade write API."),
            ("cascade_connect_template_wrong", ["cascade connections"], "Use a stream or window connect template for cascade endpoints.", "Restore cascade-compatible connection."),
            ("cascade_endpoint_index_wrong", ["cascade endpoint arrays"], "Increase a cascade endpoint index beyond declarations.", "Use a valid cascade endpoint index."),
            ("cascade_parameter_pointer_removed", ["input_cascade<T>*"], "Remove pointer syntax from a cascade parameter.", "Restore pointer syntax expected by the API."),
            ("cascade_namespace_removed", ["adf::input_cascade", "adf::output_cascade"], "Remove the adf namespace qualifier.", "Restore required namespace qualifier."),
            ("cascade_mixed_with_plio", ["cascade endpoints", "PLIO endpoints"], "Connect a cascade endpoint directly to PLIO.", "Use a compatible intermediate kernel or endpoint."),
        ],
    ),
    (
        "rtp_parameters",
        ["RTP", "parameter", "graph API"],
        [
            ("parameter_direction_swapped", ["parameter", "input", "output"], "Swap RTP parameter direction.", "Restore correct parameter direction."),
            ("parameter_type_changed", ["parameter<T>"], "Change the RTP parameter template type.", "Restore the parameter type used by the kernel."),
            ("connect_parameter_to_stream", ["parameter connections"], "Connect an RTP parameter to a stream endpoint.", "Connect RTP endpoints to compatible parameter ports."),
            ("async_parameter_missing", ["async RTP"], "Remove the async marker or declaration.", "Restore async RTP declaration."),
            ("parameter_array_index_oob", ["parameter arrays"], "Use an out-of-range RTP array index.", "Use a declared parameter index."),
            ("parameter_update_api_misspelled", ["update", "set"], "Misspell the RTP update API.", "Restore the update API spelling."),
            ("parameter_port_member_typo", ["param port members"], "Append a suffix to an RTP port member.", "Restore the declared RTP port member."),
            ("kernel_signature_missing_rtp_arg", ["kernel args", "RTP"], "Remove the kernel argument corresponding to an RTP port.", "Restore the RTP kernel parameter."),
            ("kernel_signature_extra_rtp_arg", ["kernel args", "RTP"], "Add an extra undeclared RTP argument.", "Remove the extra argument or declare/connect it properly."),
            ("rtp_constness_mismatch", ["const RTP arguments"], "Add or remove const on an RTP parameter incompatibly.", "Restore constness matching API expectations."),
        ],
    ),
    (
        "template_arguments",
        ["templates", "types", "compile-time constants"],
        [
            ("template_int_arg_non_numeric", ["template<int N>"], "Replace an integer template argument with an identifier that is not declared.", "Restore a valid integer template argument."),
            ("template_type_arg_unknown", ["template<typename T>"], "Replace a type template argument with an unknown type.", "Restore a declared type argument."),
            ("template_arg_count_missing", ["foo<A,B>"], "Remove one required template argument.", "Restore the expected number of template arguments."),
            ("template_arg_count_extra", ["foo<A>"], "Add an extra template argument.", "Remove the extra template argument."),
            ("kernel_template_value_mismatch", ["kernel template parameters"], "Change a kernel template value to conflict with buffer/vector sizes.", "Restore consistent template values."),
            ("graph_template_value_mismatch", ["graph template parameters"], "Change a graph template value to conflict with connections.", "Restore consistent graph template values."),
            ("constexpr_removed", ["constexpr int"], "Remove constexpr from a value used as a template argument.", "Restore constexpr or make the value compile-time constant."),
            ("static_const_wrong_type", ["static constexpr"], "Change a static constexpr integer to float/string.", "Restore integer constant type."),
            ("template_keyword_removed", ["template<...>"], "Remove the template keyword from a declaration.", "Restore the template declaration syntax."),
            ("dependent_template_keyword_missing", ["dependent template calls"], "Remove required dependent template keyword.", "Restore dependent template syntax."),
        ],
    ),
    (
        "namespaces_and_types",
        ["namespace", "type", "spelling"],
        [
            ("adf_namespace_removed", ["adf::graph", "adf::kernel"], "Remove adf:: from a type or API call without using namespace.", "Restore adf:: or a valid using declaration."),
            ("aie_namespace_removed", ["aie::vector", "aie::accum"], "Remove aie:: from AIE API types.", "Restore aie:: namespace qualifier."),
            ("using_namespace_removed", ["using namespace adf", "using namespace aie"], "Remove a using directive required by unqualified symbols.", "Restore the using directive or qualify symbols."),
            ("graph_base_class_misspelled", ["class G : public graph"], "Misspell graph in the base class.", "Restore graph/adf::graph base class."),
            ("kernel_type_misspelled", ["kernel"], "Misspell adf::kernel type.", "Restore kernel type spelling."),
            ("port_type_misspelled", ["port<input>", "port<output>"], "Misspell port type.", "Restore port type spelling."),
            ("fixed_width_type_misspelled", ["int16", "cint32", "uint32"], "Misspell an AIE fixed-width type.", "Restore the AIE type spelling."),
            ("class_name_constructor_mismatch", ["graph constructors"], "Change constructor name so it no longer matches the class.", "Restore constructor name matching class name."),
            ("member_name_typo", ["graph members", "kernel members", "ports"], "Append a suffix to a member reference.", "Restore the declared member name."),
            ("function_name_typo", ["kernel functions", "helper functions"], "Append a suffix to a function call.", "Restore the declared function name."),
        ],
    ),
    (
        "graph_lifecycle",
        ["graph", "main", "lifecycle"],
        [
            ("graph_instance_missing", ["graph global instance"], "Remove the graph instance declaration.", "Restore a graph instance."),
            ("graph_instance_type_wrong", ["graph global instance"], "Change the graph instance type to an unrelated class.", "Restore the correct graph class type."),
            ("init_call_misspelled", ["g.init()"], "Misspell init call.", "Restore init()."),
            ("run_call_misspelled", ["g.run()"], "Misspell run call.", "Restore run()."),
            ("end_call_misspelled", ["g.end()"], "Misspell end call.", "Restore end()."),
            ("run_argument_invalid", ["g.run(N)"], "Replace run count with an invalid identifier.", "Restore a valid run count."),
            ("main_signature_wrong", ["int main"], "Change main signature to an invalid form for graph compilation.", "Restore valid main signature."),
            ("graph_constructor_private", ["graph class public:"], "Move constructor under private access.", "Expose constructor publicly."),
            ("graph_member_uninitialized", ["kernel members", "port members"], "Remove member initialization/creation statement.", "Restore graph member initialization."),
            ("graph_class_brace_missing", ["class graph definitions"], "Remove a closing class brace or semicolon.", "Restore class closing brace and semicolon."),
        ],
    ),
    (
        "memory_tiling",
        ["memory", "tiling", "dimensions"],
        [
            ("dimensions_api_misspelled", ["dimensions"], "Misspell dimensions API.", "Restore dimensions spelling."),
            ("dimension_value_wrong", ["dimensions(...)"], "Change one dimension value to an invalid size.", "Restore supported dimensions."),
            ("tiling_parameter_count_wrong", ["tiling_parameters"], "Remove or add a tiling parameter argument.", "Restore expected tiling parameter count."),
            ("buffer_location_bank_invalid", ["bank", "memory tile"], "Change bank/location enum to an invalid symbol.", "Restore supported bank/location enum."),
            ("address_increment_wrong_type", ["ptr +=", "iterator +="], "Change address increment to a vector object or bad type.", "Restore integer address increment."),
            ("restrict_removed_from_pointer", ["restrict pointers"], "Remove restrict qualifier where API signatures require it.", "Restore required restrict qualifier."),
            ("alignment_attribute_invalid", ["alignas", "aligned"], "Change alignment to an unsupported value.", "Restore valid alignment."),
            ("aie_dm_resource_wrong", ["aie_dm_resource"], "Change DM resource annotation to an invalid resource.", "Restore supported DM resource annotation."),
            ("circular_buffer_extent_mismatch", ["circular buffer", "extents"], "Change circular buffer extent to conflict with access pattern.", "Restore matching extent."),
            ("memcpy_element_size_mismatch", ["memcpy", "vector store"], "Change element-size expression to incompatible type/size.", "Restore size expression matching data type."),
        ],
    ),
    (
        "architecture_specific",
        ["AIE", "AIE-ML", "architecture"],
        [
            ("aie_ml_only_api_on_aie", ["AIE-ML APIs"], "Use an AIE-ML-only API while targeting AIE.", "Use an API supported by the target architecture."),
            ("aie_only_api_on_aie_ml", ["AIE APIs"], "Use an AIE-only API while targeting AIE-ML.", "Use an API supported by AIE-ML or change target."),
            ("wrong_arch_header", ["AIE architecture headers"], "Swap an AIE header with an AIE-ML-specific header or vice versa.", "Restore architecture-matching header."),
            ("wrong_target_macro", ["__AIENGINE__", "architecture macros"], "Change or remove target macro guards.", "Restore correct target macro guard."),
            ("unsupported_vector_width_for_arch", ["arch-specific vector widths"], "Use a vector width unsupported by the selected architecture.", "Restore a vector width supported by target."),
            ("unsupported_accumulator_for_arch", ["arch-specific accumulators"], "Use an accumulator type unsupported by the selected architecture.", "Restore accumulator type supported by target."),
            ("wrong_platform_part_assumption", ["target part", "platform"], "Introduce code guarded for the wrong platform family.", "Restore target-compatible platform guard."),
            ("aie_ml_tile_api_wrong_namespace", ["AIE-ML tile APIs"], "Move an architecture-specific tile API into the wrong namespace.", "Restore correct namespace for target."),
            ("unsupported_datatype_for_arch", ["bfloat16", "float", "cfloat"], "Use datatype unsupported by the target architecture/API path.", "Restore a supported datatype."),
            ("architecture_guard_inverted", ["#ifdef AIE_ML", "#ifndef"], "Invert a preprocessor architecture guard.", "Restore the guard condition."),
        ],
    ),
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def build_bug_families() -> list[BugFamily]:
    families: list[BugFamily] = []
    counter = 1
    for category, tags, variants in CATEGORY_SPECS:
        for variant, match_targets, mutation_strategy, repair_expectation in variants:
            bug_type = f"{slug(category)}__{slug(variant)}"
            families.append(
                BugFamily(
                    family_id=f"BF{counter:03d}",
                    bug_type=bug_type,
                    category=category,
                    match_targets=match_targets,
                    mutation_strategy=mutation_strategy,
                    repair_expectation=repair_expectation,
                    validation_signal="The mutated project must fail WSL Vitis/AIE compilation before it can become a v7 row.",
                    tags=sorted(set(tags + [slug(variant).split("_")[0]])),
                )
            )
            counter += 1
    return families


def iter_jsonl(families: Iterable[BugFamily]) -> Iterable[str]:
    for family in families:
        yield json.dumps(asdict(family), ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the v7 AIE bug-family catalog.")
    parser.add_argument("--format", choices=["jsonl", "json", "count"], default="jsonl")
    args = parser.parse_args()

    families = build_bug_families()
    if args.format == "count":
        print(len(families))
        return 0
    if args.format == "json":
        print(json.dumps([asdict(family) for family in families], indent=2, ensure_ascii=False))
        return 0
    for line in iter_jsonl(families):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

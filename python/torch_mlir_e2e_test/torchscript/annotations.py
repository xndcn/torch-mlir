# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
# Also available under a BSD-style license. See LICENSE.

from typing import List, Optional, Tuple, NamedTuple

import torch

# Decorators

# Currently, these decorators are very low-level and map 1:1 with
# methods on `torch_mlir.ClassAnnotator`. Eventually, we expect there to
# be a more elaborate Python layer which allows all the different annotations
# to be expressed conveniently and gives clearer error reports when
# the annotations aren't acceptable.

# This module is kept separate from torch_mlir.torchscript_annotations so that
# we can use this from code without C++ dependencies, which prevent us from
# interfacing the test framework across environments.

# Attribute names used for annotations.
# These should be kept in sync with their use in
# `torch_mlir/torchscript_annotations.py`.
TORCH_MLIR_EXPORT_ATTR_NAME = '_torch_mlir_export'
TORCH_MLIR_ARG_ANNOTATIONS_ATTR_NAME = '_torch_mlir_arg_annotations'


def export(fn):
    """Decorator that tells the torch-mlir compiler that a method is exported.

    By default, no methods are exported, which is very important for
    the compiler, because otherwise most Torch programs consist of a sea
    of tiny exported functions with no rank or dtype information
    (see `annotate_args`), which the compiler cannot do much with.

    Note that this is different from `torch.jit.export`, which controls
    which methods are scripted in the first place. For non-`forward` methods,
    using this decorator usually means you also need `torch.jit.export`.
    Conceptually, this decorator is annotating the scripted module, but is
    applied to the original `torch.nn.Module` for convenience.
    """
    setattr(fn, TORCH_MLIR_EXPORT_ATTR_NAME, True)
    return fn


ArgAnnotation = Tuple[List[int], torch.dtype]


# TODO: Replace with py3 extended argument annotations when available.
# See https://www.python.org/dev/peps/pep-0593/
def annotate_args(annotations: List[Optional[ArgAnnotation]]):
    """Decorator that tells the torch-mlir compiler information about arguments.

    The `annotations` should be a list of the same length as the number of
    argument to the method (including `self`). Each list entry is either:
    - None, corresponding to providing the compiler with no information.
    - A 2-tuple consisting of a shape and a dtype, such as
      `([2, 3, 4], torch.float32)`. A dimension with an unknown size can be
      indicated by using `-1` as the size. This provides the compiler a
      guarantee that the argument will always dynamically have the described
      shape and dtype.
    """

    # TODO: Check the number of arguments matches the number of arg annotations.
    def decorator(fn):
        setattr(fn, TORCH_MLIR_ARG_ANNOTATIONS_ATTR_NAME, annotations)
        return fn

    return decorator


class SerializableMethodAnnotation(NamedTuple):
    method_name: str
    export: Optional[bool]
    arg_annotations: Optional[List[ArgAnnotation]]


class SerializableModuleAnnotations(NamedTuple):
    method_annotations: List[SerializableMethodAnnotation]
    submodule_annotations: List[Tuple[str, "SerializableModuleAnnotations"]]


def extract_serializable_annotations(
        module: torch.nn.Module) -> SerializableModuleAnnotations:
    module_annotations = SerializableModuleAnnotations(
        method_annotations=[], submodule_annotations=[])
    # Extract information on methods.
    for method_name, method in module.__dict__.items():
        # See if it is a method.
        if not callable(method):
            continue
        export = None
        arg_annotations = None
        if hasattr(method, TORCH_MLIR_EXPORT_ATTR_NAME):
            export = method._torch_mlir_export
        if hasattr(method, TORCH_MLIR_ARG_ANNOTATIONS_ATTR_NAME):
            arg_annotations = method._torch_mlir_arg_annotations
        if export is not None and arg_annotations is not None:
            module_annotations.method_annotations.append(
                SerializableMethodAnnotation(method_name=method_name,
                                             export=export,
                                             arg_annotations=arg_annotations))

    # Recurse.
    for name, child in module.named_children():
        annotations = extract_serializable_annotations(child)
        module_annotations.submodule_annotations.append((name, annotations))
    return module_annotations


def apply_serializable_annotations(module: torch.nn.Module,
                                   annotations: SerializableModuleAnnotations):
    # Apply annotations to methods.
    for method_annotation in annotations.method_annotations:
        # Imitate use of the decorators to keep a source of truth there.
        if method_annotation.export is not None:
            setattr(module, method_annotation.method_name,
                    export(getattr(module, method_annotation.method_name)))
        if method_annotation.arg_annotations is not None:
            setattr(
                module, method_annotation.method_name,
                annotate_args(method_annotation.arg_annotations)(getattr(
                    module, method_annotation.method_name)))

    # Recurse.
    for name, submodule_annotations in annotations.submodule_annotations:
        child = getattr(module, name)
        apply_serializable_annotations(child, submodule_annotations)

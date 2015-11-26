// Copyright (c) 2015 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "base/bind.h"
#include "media/formats/mp2t/es_parser_adts.h"

static void NewAudioConfig(const media::AudioDecoderConfig& config) {}
static void EmitBuffer(scoped_refptr<media::StreamParserBuffer> buffer) {}

// Entry point for LibFuzzer.
extern "C" int LLVMFuzzerTestOneInput(const unsigned char* data, size_t size) {
  media::mp2t::EsParserAdts es_parser(base::Bind(&NewAudioConfig),
                                      base::Bind(&EmitBuffer), true);
  if (!es_parser.Parse(data, size, media::kNoTimestamp(),
                       media::kNoDecodeTimestamp())) {
    return 0;
  }
  es_parser.Flush();
  return 0;
}

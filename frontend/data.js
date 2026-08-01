/* Static offline fixture, generated from a real `python -m playground.run`.
   No request is made to obtain or update this data. */
window.PLAYGROUND_DATA = {
  "schema_version": "2.0",
  "run_id": "offline-demo",
  "run": {
    "run_id": "offline-demo",
    "source_title": "On Calibration of Modern Neural Networks",
    "input_kind": "pdf"
  },
  "mode": "quantitative",
  "spans": {
    "p1_b32": {
      "page": 1,
      "kind": "paragraph",
      "section": "abstract",
      "text": "Conﬁdence calibration – the problem of predict- ing probability estimates representative of the true correctness likelihood – is important for classiﬁcation models in many applications. We discover that modern neural networks, unlike those from a decade ago, are poorly calibrated. Through extensive experiments, we observe that depth, width, weight decay, and Batch Normal- ization are important factors inﬂuencing calibra- tion. We evaluate the performance of various post-processing calibration methods on state-of- the-art architectures with image and document classiﬁcation datasets. Our analysis and exper- iments not only offer insights into neural net- work learning, but also provide a simple and straightforward recipe for practical settings: on most datasets, temperature scaling – a single- parameter variant of Platt Scaling – is surpris- ingly effective at calibrating predictions."
    },
    "p4_b14": {
      "page": 4,
      "kind": "paragraph",
      "section": "results",
      "text": "NLL can be used to indirectly measure model calibra- tion. In practice, we observe a disconnect between NLL and accuracy, which may explain the miscalibration in Fig- ure 2. This disconnect occurs because neural networks can overﬁt to NLL without overﬁtting to the 0/1 loss. We ob- serve this trend in the training curves of some miscalibrated models. Figure 3 shows test error and NLL (rescaled to match error) on CIFAR-100 as training progresses. Both error and NLL immediately drop at epoch 250, when the learning rate is dropped; however, NLL overﬁts during the remainder of training. Surprisingly, overﬁtting to NLL is beneﬁcial to classiﬁcation accuracy. On CIFAR-100, test error drops from 29% to 27% in the region where NLL overﬁts. This phenomenon renders a concrete explanation of miscalibration: the network learns better classiﬁcation accuracy at the expense of well-modeled probabilities."
    },
    "p4_b18": {
      "page": 4,
      "kind": "paragraph",
      "section": "methods",
      "text": "In this section, we ﬁrst review existing calibration meth- ods, and introduce new variants of our own. All methods are post-processing steps that produce (calibrated) proba- bilities. Each method requires a hold-out validation set, which in practice can be the same set used for hyperparam- eter tuning. We assume that the training, validation, and test sets are drawn from the same distribution."
    },
    "p6_b2": {
      "page": 6,
      "kind": "paragraph",
      "section": "methods",
      "text": "20 News DAN 3 8.02% 3.6% 5.52% 4.98% 4.11% 4.61% 9.1% Reuters DAN 3 0.85% 1.75% 1.15% 0.97% 0.91% 0.66% 1.58% SST Binary TreeLSTM 6.63% 1.93% 1.65% 2.27% 1.84% 1.84% 1.84% SST Fine Grained TreeLSTM 6.71% 2.09% 1.65% 2.61% 2.56% 2.98% 2.39%"
    },
    "p6_b13": {
      "page": 6,
      "kind": "paragraph",
      "section": "other",
      "text": "Temperature scaling, the simplest extension of Platt scaling, uses a single scalar parameter T > 0 for all classes. Given the logit vector zi, the new conﬁdence prediction is"
    },
    "p7_b7": {
      "page": 7,
      "kind": "paragraph",
      "section": "results",
      "text": "Calibration Results. Table 1 displays model calibration, as measured by ECE (with M = 15 bins), before and af- ter applying the various methods (see Section S3 for MCE, NLL, and error tables). It is worth noting that most datasets and models experience some degree of miscalibration, with ECE typically between 4 to 10%. This is not architecture speciﬁc: we observe miscalibration on convolutional net- works (with and without skip connections), recurrent net- works, and deep averaging networks. The two notable ex- ceptions are SVHN and Reuters, both of which experience ECE values below 1%. Both of these datasets have very low error (1.98% and 2.97%, respectively); and therefore the ratio of ECE to error is comparable to other datasets."
    },
    "p7_b10": {
      "page": 7,
      "kind": "paragraph",
      "section": "results",
      "text": "Our most important discovery is the surprising effective- ness of temperature scaling despite its remarkable simplic- ity. Temperature scaling outperforms all other methods on the vision tasks, and performs comparably to other methods on the NLP datasets. What is perhaps even more surpris- ing is that temperature scaling outperforms the vector and matrix Platt scaling variants, which are strictly more gen- eral methods. In fact, vector scaling recovers essentially the same solution as temperature scaling – the learned vec- tor has nearly constant entries, and therefore is no different than a scalar transformation. In other words, network mis- calibration is intrinsically low dimensional."
    },
    "p8_b31": {
      "page": 8,
      "kind": "paragraph",
      "section": "results",
      "text": "Ease of implementation. BBQ is arguably the most dif- ﬁcult to implement, as it requires implementing a model averaging scheme. While all other methods are relatively easy to implement, temperature scaling may arguably be the most straightforward to incorporate into a neural net- work pipeline. In Torch7 (Collobert et al., 2011), for ex- ample, we implement temperature scaling by inserting a nn.MulConstant between the logits and the softmax, whose parameter is 1/T. We set T =1 during training, and subsequently ﬁnd its optimal value on the validation set."
    },
    "p8_b34": {
      "page": 8,
      "kind": "paragraph",
      "section": "discussion",
      "text": "Modern neural networks exhibit a strange phenomenon: probabilistic error and miscalibration worsen even as clas- siﬁcation error is reduced. We have demonstrated that recent advances in neural network architecture and train- ing – model capacity, normalization, and regularization – have strong effects on network calibration. It remains future work to understand why these trends affect cali- bration while improving accuracy. Nevertheless, simple techniques can effectively remedy the miscalibration phe- nomenon in neural networks. Temperature scaling is the simplest, fastest, and most straightforward of the methods, and surprisingly is often the most effective."
    }
  },
  "artifact": {
    "primitive": "interactive_explainer",
    "mode": "quantitative",
    "title": "On Calibration of Modern Neural Networks",
    "thesis": "Conﬁdence calibration – the problem of predict- ing probability estimates representative of the true correctness likelihood – is important for classiﬁcation models in many applications. We discover that modern neural networks, unlike those from a decade ago, are poorly calibrated. Through extensive experiments, we observe that depth, width, weight decay, and Batch Normal- ization are important factors inﬂuencing calibra- tion. We evaluate the performance of various post-processing calibration methods on state-of- the-art architectures with image and document classiﬁcation datasets. Our analysis and exper- iments not only offer insights into neural net- work learning, but also provide a simpl",
    "bottleneck": {
      "question": "정확도는 좋아지는데 확률 예측 품질은 왜 나빠질 수 있을까?",
      "why_hard": "정확도와 confidence가 서로 다른 성질이라는 점이 여러 정의·결과 문단에 나뉘어 있다.",
      "source_claim_ids": [
        "c1"
      ],
      "evidence_refs": [
        "p1_b32",
        "p4_b14",
        "p7_b7",
        "p8_b34",
        "p7_b10",
        "p6_b13",
        "p8_b31"
      ],
      "mechanism_kind": "calibration",
      "candidate_controls": [
        "temperature"
      ],
      "candidate_observables": [
        "correctness",
        "confidence"
      ],
      "learning_payoff": 0.95,
      "data_sufficiency": "sufficient",
      "fidelity": "high"
    },
    "panels": [
      {
        "primitive": "generated_schematic",
        "question": "정답 여부와 확신의 정도는 같은 값일까?",
        "model": {
          "type": "state_graph",
          "nodes": [
            "예측",
            "정답 여부",
            "confidence"
          ],
          "edges": [
            [
              "예측",
              "정답 여부"
            ],
            [
              "예측",
              "confidence"
            ]
          ]
        },
        "controls": [],
        "observables": [
          {
            "name": "correctness",
            "label": "맞혔는가"
          },
          {
            "name": "confidence",
            "label": "얼마나 확신했는가"
          }
        ],
        "feedback": {
          "default": "맞힌 비율과 확신이 잘 맞는지는 별도로 확인해야 합니다."
        },
        "provenance": [
          {
            "kind": "caption_direction",
            "provenance": "source_stated",
            "precision": "qualitative",
            "source_refs": [
              "p1_b32",
              "p4_b14",
              "p7_b7",
              "p8_b34",
              "p7_b10",
              "p6_b13",
              "p8_b31"
            ]
          }
        ],
        "notice": "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다."
      },
      {
        "primitive": "scaling_comparison",
        "question": "temperature T를 바꾸면 confidence가 어떻게 달라질까?",
        "model": {
          "type": "formula",
          "expression": "softmax(logits / T)",
          "parameters": {
            "T": {
              "min": 0.5,
              "max": 5.0,
              "default": 1.0
            }
          },
          "allowed_ops": [
            "+",
            "-",
            "*",
            "/",
            "pow",
            "min",
            "max",
            "log"
          ]
        },
        "controls": [
          {
            "name": "T",
            "kind": "slider",
            "min": 0.5,
            "max": 5.0,
            "default": 1.0,
            "provenance": "illustrative",
            "precision": "qualitative"
          }
        ],
        "observables": [
          {
            "name": "confidence",
            "label": "confidence"
          }
        ],
        "feedback": {
          "low": "T가 작아지면 분포가 뾰족해져 확신이 커집니다.",
          "high": "T가 커지면 분포가 평평해져 과한 확신을 누그러뜨립니다."
        },
        "provenance": [
          {
            "kind": "formula",
            "provenance": "source_stated",
            "precision": "exact",
            "source_refs": [
              "p1_b32",
              "p4_b14",
              "p7_b7",
              "p8_b34",
              "p7_b10",
              "p6_b13",
              "p8_b31"
            ]
          }
        ],
        "notice": "수식에 따른 설명용 모델입니다. 원문 곡선을 재생하지 않습니다."
      }
    ],
    "comparison": {
      "available": false,
      "reason": "figure 픽셀 수치는 자동 복원하지 않음"
    },
    "glossary": [
      {
        "term": "calibration",
        "definition": "예측 확률이 실제 정답 비율과 얼마나 맞는지"
      },
      {
        "term": "temperature scaling",
        "definition": "logit 분포의 날카로움을 T로 조절하는 방법"
      }
    ],
    "summary": [
      "정확도를 잘 맞히는 것과 확률을 믿을 만하게 말하는 것은 다릅니다.",
      "temperature scaling은 confidence의 모양을 조절합니다."
    ],
    "critical_note": {
      "title": "원문과 설명 모델의 경계",
      "text": "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다.",
      "conditions": [
        {
          "text": "평가 지표는 논문이 선택한 calibration bin 설정을 따른다.",
          "weakens_how": "bin 수와 간격을 바꾸면 ECE와 MCE가 달라져 방법 간 순위가 동일하게 유지된다고 말할 수 없다.",
          "span_id": "p6_b2",
          "source": "paper_explicit"
        },
        {
          "text": "temperature scaling은 별도 validation 데이터로 fit된다.",
          "weakens_how": "test 데이터에 temperature를 맞추면 calibration 수치가 낙관적으로 치우쳐 독립 평가라는 주장이 약해진다.",
          "span_id": "p4_b18",
          "source": "paper_explicit"
        }
      ]
    },
    "editorial": {
      "hook": "결과 숫자 하나만 보면 놓치기 쉬운 연결고리를 직접 움직여 봅니다.",
      "instruction": "슬라이더를 움직이고, 무엇이 바뀌는지 한 문장으로 확인하세요.",
      "caveat": "설명용 도식이며 원문 figure를 픽셀 단위로 재현한 것이 아닙니다.",
      "language": "ko"
    },
    "sources": [
      {
        "span_id": "p1_b32",
        "page": 1,
        "kind": "paragraph"
      },
      {
        "span_id": "p4_b14",
        "page": 4,
        "kind": "paragraph"
      },
      {
        "span_id": "p7_b7",
        "page": 7,
        "kind": "paragraph"
      },
      {
        "span_id": "p8_b34",
        "page": 8,
        "kind": "paragraph"
      },
      {
        "span_id": "p7_b10",
        "page": 7,
        "kind": "paragraph"
      },
      {
        "span_id": "p6_b13",
        "page": 6,
        "kind": "paragraph"
      },
      {
        "span_id": "p8_b31",
        "page": 8,
        "kind": "paragraph"
      }
    ],
    "external_visualization": null,
    "external": []
  },
  "external": [],
  "analysis": {
    "note": "claim lineage는 내부 추론 산출물이라 fixture에서는 생략합니다."
  },
  "raw_events": [
    {
      "id": "161b87e4",
      "ts": 0,
      "type": "stage_start",
      "stage": "parse"
    },
    {
      "id": "edf37b57",
      "ts": 0,
      "type": "tool_call",
      "name": "pdf.extract",
      "arguments": {
        "path": "fixtures/guo17a.pdf"
      }
    },
    {
      "id": "0c9411a8",
      "ts": 0,
      "type": "tool_result",
      "call_id": "edf37b57",
      "result": {
        "pages": 10,
        "spans": 332,
        "figures": 0,
        "numbers": 729,
        "kinds": {
          "table_cell": 113,
          "paragraph": 198,
          "caption": 5,
          "equation": 16
        }
      },
      "error": null
    },
    {
      "id": "830cfa51",
      "ts": 0,
      "type": "decision",
      "actor": "parse",
      "text": "729개 수치를 근거 풀에 등록",
      "kinds": {
        "table_cell": 113,
        "paragraph": 198,
        "caption": 5,
        "equation": 16
      },
      "claim_seed": false
    },
    {
      "id": "73bcd4db",
      "ts": 0,
      "type": "stage_end",
      "stage": "parse",
      "seconds": 1.357,
      "over_budget": false
    },
    {
      "id": "42bf2405",
      "ts": 0,
      "type": "stage_start",
      "stage": "context"
    },
    {
      "id": "5306ef61",
      "ts": 0,
      "type": "tool_call",
      "name": "llm.structured",
      "arguments": {
        "role": "context_analyst",
        "prompt_chars": 41920,
        "schema": "ContextAnalysis"
      }
    },
    {
      "id": "110afca4",
      "ts": 0,
      "type": "tool_result",
      "call_id": "5306ef61",
      "result": {},
      "error": null
    },
    {
      "id": "25f7c54c",
      "ts": 0,
      "type": "decision",
      "actor": "context",
      "text": "원문 기반 context 후보 생성",
      "claims": [
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6"
      ],
      "mechanism": "calibration"
    },
    {
      "id": "e4f75b80",
      "ts": 0,
      "type": "decision",
      "actor": "context",
      "text": "큰 context 분석 응답 없음 -> 원문 bound 분석 사용"
    },
    {
      "id": "2be021a9",
      "ts": 0,
      "type": "decision",
      "actor": "context",
      "text": "source context semantic envelope 준비",
      "claims": 6,
      "mechanisms": 1,
      "bottleneck": true,
      "source_refs": 7
    },
    {
      "id": "039dc276",
      "ts": 0,
      "type": "stage_end",
      "stage": "context",
      "seconds": 0.007,
      "over_budget": false
    },
    {
      "id": "63b18416",
      "ts": 0,
      "type": "stage_start",
      "stage": "claims"
    },
    {
      "id": "99ebb065",
      "ts": 0,
      "type": "decision",
      "actor": "claims",
      "text": "context analysis의 구조화 claim 사용",
      "proposed_claims": 6
    },
    {
      "id": "a502bc71",
      "ts": 0,
      "type": "decision",
      "actor": "claims",
      "text": "명시 root 없음 -> 유일한 parent 없는 node 사용",
      "root_claim_id": "c1"
    },
    {
      "id": "f3f149a5",
      "ts": 0,
      "type": "decision",
      "actor": "claims",
      "text": "후보 6개 중 6개 graph node 채택 (폐기 0개)",
      "proposed": 6,
      "accepted": 6
    },
    {
      "id": "2cf42672",
      "ts": 0,
      "type": "stage_end",
      "stage": "claims",
      "seconds": 0.002,
      "over_budget": false
    },
    {
      "id": "a7842b88",
      "ts": 0,
      "type": "stage_start",
      "stage": "score"
    },
    {
      "id": "7e40faf3",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c1 score=0.57 frontier=0.58",
      "claim_id": "c1",
      "grounded": false,
      "frontier_score": 0.583
    },
    {
      "id": "de6f435c",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c2 score=0.82 frontier=0.77",
      "claim_id": "c2",
      "grounded": true,
      "frontier_score": 0.774
    },
    {
      "id": "4bd88981",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c3 score=0.82 frontier=0.79",
      "claim_id": "c3",
      "grounded": true,
      "frontier_score": 0.788
    },
    {
      "id": "c35a0d36",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c4 score=0.52 frontier=0.60",
      "claim_id": "c4",
      "grounded": false,
      "frontier_score": 0.599
    },
    {
      "id": "9e681791",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c5 score=0.77 frontier=0.78",
      "claim_id": "c5",
      "grounded": true,
      "frontier_score": 0.778
    },
    {
      "id": "6784d24d",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "c6 score=0.77 frontier=0.79",
      "claim_id": "c6",
      "grounded": true,
      "frontier_score": 0.792
    },
    {
      "id": "ffe04731",
      "ts": 0,
      "type": "decision",
      "actor": "scorer",
      "text": "4개 claim이 number_pool과 매칭 -> quantitative 모드"
    },
    {
      "id": "a434606c",
      "ts": 0,
      "type": "stage_end",
      "stage": "score",
      "seconds": 0.001,
      "over_budget": false
    },
    {
      "id": "70af6acc",
      "ts": 0,
      "type": "stage_start",
      "stage": "select"
    },
    {
      "id": "c89c0f9e",
      "ts": 0,
      "type": "decision",
      "actor": "selector",
      "text": "c6: pedagogic frontier 자동 선택",
      "claim_id": "c6",
      "score": 0.77,
      "frontier_score": 0.792,
      "root_claim_id": "c1",
      "critical_path_ids": [
        "c1",
        "c6"
      ],
      "policy": "highest_frontier_score_then_graph_order",
      "candidates": [
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6"
      ]
    },
    {
      "id": "f2b95b2a",
      "ts": 0,
      "type": "stage_end",
      "stage": "select",
      "seconds": 0.0,
      "over_budget": false
    },
    {
      "id": "2ef22253",
      "ts": 0,
      "type": "stage_start",
      "stage": "bottleneck"
    },
    {
      "id": "d4b8fe96",
      "ts": 0,
      "type": "decision",
      "actor": "bottleneck",
      "text": "context analysis의 병목 사용",
      "question": "정확도는 좋아지는데 확률 예측 품질은 왜 나빠질 수 있을까?",
      "mechanism_kind": "calibration"
    },
    {
      "id": "f5113e49",
      "ts": 0,
      "type": "stage_end",
      "stage": "bottleneck",
      "seconds": 0.0,
      "over_budget": false
    },
    {
      "id": "60f705f4",
      "ts": 0,
      "type": "stage_start",
      "stage": "router"
    },
    {
      "id": "10bc312a",
      "ts": 0,
      "type": "tool_call",
      "name": "llm.structured",
      "arguments": {
        "role": "primitive_router",
        "prompt_chars": 33,
        "schema": "PrimitiveRoute"
      }
    },
    {
      "id": "e139d359",
      "ts": 0,
      "type": "tool_result",
      "call_id": "10bc312a",
      "result": {},
      "error": null
    },
    {
      "id": "26d47438",
      "ts": 0,
      "type": "decision",
      "actor": "router",
      "text": "허용 primitive 선택",
      "route": "calibration_explainer",
      "max_panels": 3,
      "source_figure_vision": false
    },
    {
      "id": "fce8559e",
      "ts": 0,
      "type": "stage_end",
      "stage": "router",
      "seconds": 0.0,
      "over_budget": false
    },
    {
      "id": "c7c555e9",
      "ts": 0,
      "type": "stage_start",
      "stage": "assumptions"
    },
    {
      "id": "dab013f2",
      "ts": 0,
      "type": "tool_call",
      "name": "llm.structured",
      "arguments": {
        "role": "assumption_miner",
        "prompt_chars": 83740,
        "schema": "Assumption[]"
      }
    },
    {
      "id": "f5bfadf4",
      "ts": 0,
      "type": "tool_result",
      "call_id": "dab013f2",
      "result": {
        "assumptions": [
          {
            "id": "a1",
            "text": "평가 지표는 논문이 선택한 calibration bin 설정을 따른다.",
            "kind": "measurement",
            "source": "paper_explicit",
            "span_id": "p6_b2",
            "weakens_how": "bin 수와 간격을 바꾸면 ECE와 MCE가 달라져 방법 간 순위가 동일하게 유지된다고 말할 수 없다."
          },
          {
            "id": "a2",
            "text": "temperature scaling은 별도 validation 데이터로 fit된다.",
            "kind": "implementation",
            "source": "paper_explicit",
            "span_id": "p4_b18",
            "weakens_how": "test 데이터에 temperature를 맞추면 calibration 수치가 낙관적으로 치우쳐 독립 평가라는 주장이 약해진다."
          }
        ]
      },
      "error": null
    }
  ]
};

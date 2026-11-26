"""Explicit in-memory session, defensive reads and single-tool atomic publication.

Legacy dictionaries are transient boundary snapshots, never a second store.
Uploaded JSON/list payloads retain their format; derived frames remain frames.
No geometry validation/projection policy is introduced here.
"""
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
import json
import re
from threading import RLock
from .tasks import TaskJournal
from .diagnostics import RuntimeDiagnostics

from .contracts import (Dataset, Layer, ToolResult, new_id, UNSPECIFIED_CRS,
                        data_crs, normalize_crs, validate_dataset_crs)


SESSION_SCHEMA_VERSION = 2


class AmbiguousReference(ValueError):
    pass


class LegacyLayerView(Sequence):
    """Live read-only compatibility projection; each read is a detached snapshot."""
    def __init__(self, session, visible):
        self._session, self._visible = session, visible

    def _snapshot(self):
        return self._session._legacy_projection(self._visible)

    def __len__(self):
        with self._session.lock:
            return (sum(layer.visible for layer in self._session._layers.values())
                    if self._visible else len(self._session._uploads))

    def __getitem__(self, index):
        return self._snapshot()[index]

    def __iter__(self):
        return iter(self._snapshot())

    def __eq__(self, other):
        return self._snapshot() == list(other)

    def __deepcopy__(self, memo):
        return deepcopy(self._snapshot(), memo)


class SessionData:
    def __init__(self):
        self.session_id = new_id()
        self.lock = RLock()
        self.task_journal = TaskJournal()
        self.diagnostics = RuntimeDiagnostics()
        self._next_layer_number = 1
        self.reply_language_preference = None
        self._datasets = {}
        self._layers = {}
        self._uploads = []
        self._attribute_relations = []
        self._results = []
        self._show_basemap = True
        self._basemap_revision = 0
        self._map_title = ""
        self._export_artifact = None
        self._export_revision = 0
        self.raw_view = LegacyLayerView(self, False)
        self.map_view = LegacyLayerView(self, True)

    @property
    def datasets(self):
        with self.lock:
            return {key: self._copy_dataset(d) for key, d in self._datasets.items()}

    @classmethod
    def ensure_compatible(cls, session):
        """Upgrade a live session in place before any UI/tool accesses new fields.

        Streamlit may retain instances of the previous module's class even
        after importing the updated module. Keep identity, data, and locks;
        adopt the current methods and rebuild only compatibility view wrappers.
        """
        changed = False
        previous_type = type(session)
        if previous_type is not cls and (previous_type.__module__, previous_type.__name__) == (cls.__module__, cls.__name__):
            session.__class__ = cls
            changed = True
        with session.lock:
            for name, factory in (("diagnostics", RuntimeDiagnostics), ("task_journal", TaskJournal)):
                if not hasattr(session, name):
                    setattr(session, name, factory())
                    changed = True
            if not hasattr(session, "reply_language_preference"):
                session.reply_language_preference = None
                changed = True
            for name, visible in (("raw_view", False), ("map_view", True)):
                view = getattr(session, name, None)
                if not isinstance(view, LegacyLayerView) or view._session is not session:
                    setattr(session, name, LegacyLayerView(session, visible))
                    changed = True
            changed = cls.ensure_layer_numbers(session) or changed
        return changed

    @staticmethod
    def _numbered_layers(layers):
        """Display positions are dense; immutable layer IDs retain identity."""
        numbered = {}
        for number, (key, layer) in enumerate(layers.items(), 1):
            numbered[key] = (layer if getattr(layer, "display_number", None) == number else
                             Layer(layer.dataset_id, layer.name, layer.renderer, layer.data_field,
                                   deepcopy(layer.style), layer.layer_id, layer.visible, number))
        return numbered

    def ensure_layer_numbers(self):
        """Normalize old sessions and keep current sidebar positions contiguous."""
        with self.lock:
            layers = self._numbered_layers(self._layers)
            changed = any(layers[key] is not self._layers[key] for key in layers)
            self._layers = layers
            self._next_layer_number = len(layers) + 1
            return changed

    @property
    def layers(self):
        with self.lock:
            return deepcopy(self._layers)

    @property
    def results(self):
        with self.lock:
            return deepcopy(self._results)

    @property
    def attribute_relations(self):
        """Temporary attribute-field mappings created in this session."""
        with self.lock:
            return deepcopy(self._attribute_relations)

    def commit_attribute_relation(self, relation, *, message, tool_name):
        """Store or replace a session-local mapping after its match report succeeds."""
        with self.lock:
            stored = deepcopy(relation)
            identity = (stored["source_dataset_id"], stored["target_dataset_id"],
                        stored["source_field"], stored["target_field"])
            relations = [item for item in self._attribute_relations if (
                item["source_dataset_id"], item["target_dataset_id"],
                item["source_field"], item["target_field"]) != identity]
            relations.append(stored)
            result = ToolResult(True, message, metrics=deepcopy(stored.get("metrics", {})))
            self._attribute_relations = relations
            self._results.append(result)
            return deepcopy(result)

    @property
    def export_artifact(self):
        with self.lock:
            return deepcopy(self._export_artifact)

    @property
    def export_revision(self):
        with self.lock:
            return self._export_revision

    def clear_export_artifact(self):
        with self.lock:
            self._export_artifact = None

    def commit_export_artifact(self, artifact, *, message, tool_name):
        with self.lock:
            stored = {
                "data": bytes(artifact["data"]),
                "file_name": str(artifact["file_name"]),
                "mime_type": str(artifact["mime_type"]),
            }
            result = ToolResult(True, message)
            self._export_artifact = stored
            self._export_revision += 1
            self._results.append(result)
            return deepcopy(result)

    @property
    def show_basemap(self):
        with self.lock:
            return self._show_basemap

    @property
    def map_title(self):
        with self.lock:
            return self._map_title

    @property
    def basemap_revision(self):
        with self.lock:
            return self._basemap_revision

    def set_basemap_visibility(self, visible):
        with self.lock:
            visible = bool(visible)
            if visible != self._show_basemap:
                self._show_basemap = visible
                self._basemap_revision += 1
            return self._show_basemap

    def get_dataset(self, dataset_id):
        with self.lock:
            return self._copy_dataset(self._datasets[dataset_id])

    def get_layer(self, layer_id):
        with self.lock:
            return deepcopy(self._layers[layer_id])

    @staticmethod
    def _copy_dataset(dataset):
        data = deepcopy(dataset.data)
        # pandas deep copies arrays, but object cells can retain mutable Python
        # references. Copy these too without importing pandas or GeoPandas here.
        if hasattr(data, "geometry") and hasattr(data, "columns"):
            for column in data.columns:
                if column != data.geometry.name and data[column].dtype == object:
                    data[column] = data[column].map(deepcopy)
        return replace(dataset, data=data, metadata=deepcopy(dataset.metadata))

    @staticmethod
    def _payload(dataset):
        data = dataset.data
        if hasattr(data, "to_json") and hasattr(data, "geometry"):
            value = json.loads(data.to_json())
            return value["features"] if dataset.metadata.get("format") == "features" else value
        return deepcopy(data)

    def _upload_summary(self, key):
        dataset = self._datasets[key]
        return {**deepcopy(dataset.metadata), "name": dataset.name,
                "type": dataset.metadata.get("type", "geojson"), "dataset_id": key, "crs": dataset.crs}

    def _layer_summary(self, layer):
        return {"name": layer.name, "type": layer.renderer, "data_field": layer.data_field,
                "folium_kwargs": deepcopy(layer.style), "layer_id": layer.layer_id,
                "dataset_id": layer.dataset_id, "layer_number": layer.display_number,
                "crs": self._datasets[layer.dataset_id].crs, "visible": layer.visible}

    @property
    def upload_summaries(self):
        with self.lock:
            return [self._upload_summary(key) for key in self._uploads]

    @property
    def layer_summaries(self):
        with self.lock:
            return [self._layer_summary(layer) for layer in self._layers.values()]

    def runtime_stats(self):
        """Cheap counts for diagnosis; never copy/serialize dataset contents."""
        with self.lock:
            mapped = {layer.dataset_id for layer in self._layers.values()}
            return {"session_id": self.session_id, "datasets": len(self._datasets),
                    "uploads": len(self._uploads), "map_layers": len(self._layers),
                    "visible_layers": sum(layer.visible for layer in self._layers.values()),
                    "unmapped_datasets": len(set(self._datasets) - mapped), "tool_results": len(self._results),
                    "export_bytes": len(self._export_artifact["data"]) if self._export_artifact else 0}

    def _legacy_projection(self, visible):
        with self.lock:
            if visible:
                entries = [self._layer_summary(layer) for layer in self._layers.values() if layer.visible]
                for entry in entries:
                    entry.pop("visible")  # Preserve the legacy visible-view contract.
            else:
                entries = [self._upload_summary(key) for key in self._uploads]
            selected = {entry["dataset_id"] for entry in entries}
            with self.diagnostics.measure("payload_projection", datasets=len(selected), visible=visible):
                payloads = {key: self._payload(self._datasets[key]) for key in selected}
            return [{**entry, "data": payloads[entry["dataset_id"]]} for entry in entries]

    def legacy_layers(self):
        with self.lock:
            return self._legacy_projection(False), self._legacy_projection(True)

    @property
    def layer_management_view(self):
        """Return every displayed layer with its visibility for the UI controls."""
        with self.lock:
            selected = {layer.dataset_id for layer in self._layers.values()}
            with self.diagnostics.measure("payload_projection", datasets=len(selected), visible="managed"):
                payloads = {key: self._payload(self._datasets[key]) for key in selected}
            return [{**self._layer_summary(layer), "data": payloads[layer.dataset_id]}
                    for layer in self._layers.values()]

    def get_agent_layers(self):
        return self.raw_view, self.map_view

    def add_dataset(self, dataset, *, uploaded=False):
        with self.lock:
            if dataset.dataset_id in self._datasets:
                raise ValueError("Dataset ID already exists")
            if any(key not in self._datasets for key in dataset.parent_ids):
                raise ValueError("Unknown parent dataset")
            dataset = replace(dataset, crs=validate_dataset_crs(dataset))
            self._datasets[dataset.dataset_id] = self._copy_dataset(dataset)
            if uploaded:
                self._uploads.append(dataset.dataset_id)
            return dataset.dataset_id

    def add_upload(self, name, data, *, file_id=None, renderer="geojson", data_fields=(), crs=UNSPECIFIED_CRS):
        """Own unmodified source data; optional crs is a declaration, not projection.

        Omitted context reads native/GeoJSON declarations. Explicit None stays
        unknown and cannot later become an implicit WGS84 tool input.
        """
        if crs is UNSPECIFIED_CRS:
            crs = data_crs(data)
            if crs is UNSPECIFIED_CRS:
                crs = "EPSG:4326"  # Existing GeoJSON / lat-lon wire contract.
        crs = normalize_crs(crs)
        d = Dataset(name, data, crs=crs, source=file_id or "upload", metadata={
            "type": renderer, "file_id": file_id or name, "data_fields": list(data_fields)})
        return self.add_dataset(d, uploaded=True)

    def add_layer(self, layer):
        with self.lock:
            if layer.dataset_id not in self._datasets or layer.layer_id in self._layers:
                raise ValueError("Unknown dataset or duplicate layer ID")
            number = len(self._layers) + 1
            self._layers[layer.layer_id] = deepcopy(replace(layer, display_number=number))
            self._next_layer_number = number + 1
            return layer.layer_id

    def remove_layer(self, layer_id):
        with self.lock:
            layers = dict(self._layers)
            del layers[layer_id]  # Data survives other renderings and lineage.
            self._layers = self._numbered_layers(layers)
            self._next_layer_number = len(layers) + 1

    def set_layer_visibility(self, layer_id, visible):
        with self.lock:
            layer = self._layers[layer_id]
            self._layers[layer_id] = replace(layer, visible=bool(visible))
            return self._layers[layer_id].visible

    def remove_upload(self, dataset_id):
        with self.lock:
            self._uploads.remove(dataset_id)  # Detach upload; displayed data survives.

    def delete_dataset(self, dataset_id, *, cascade=False):
        with self.lock:
            if dataset_id not in self._datasets:
                raise KeyError(dataset_id)
            children = [k for k, d in self._datasets.items() if dataset_id in d.parent_ids]
            layers = [k for k, l in self._layers.items() if l.dataset_id == dataset_id]
            if (children or layers) and not cascade:
                raise ValueError("Dataset is still referenced")
            for key in children:
                if key in self._datasets:
                    self.delete_dataset(key, cascade=True)
            for key in layers:
                del self._layers[key]
            if dataset_id in self._uploads:
                self._uploads.remove(dataset_id)
            self._attribute_relations = [relation for relation in self._attribute_relations
                                         if dataset_id not in (relation["source_dataset_id"], relation["target_dataset_id"])]
            del self._datasets[dataset_id]
            self._layers = self._numbered_layers(self._layers)
            self._next_layer_number = len(self._layers) + 1

    def reset(self):
        with self.lock:
            self.task_journal.reset()
            self._next_layer_number = 1
            self.reply_language_preference = None
            self._datasets.clear()
            self._layers.clear()
            self._uploads.clear()
            self._attribute_relations.clear()
            self._results.clear()
            self._export_artifact = None
            self._map_title = ""

    def replace_from_legacy(self, raw, shown):
        """Import an explicit old context into this session; retain no input refs."""
        replacement = SessionData()
        by_identity = {}
        for entry in raw:
            key = replacement.add_upload(entry["name"], entry["data"], file_id=entry.get("file_id"),
                                         renderer=entry.get("type", "geojson"), data_fields=entry.get("data_fields", ()),
                                         crs=entry.get("crs", UNSPECIFIED_CRS))
            by_identity[id(entry["data"])] = key
        for entry in shown:
            key = by_identity.get(id(entry["data"]))
            if key is None:
                reference = entry["crs"] if "crs" in entry else data_crs(entry["data"])
                if reference is UNSPECIFIED_CRS:
                    reference = "EPSG:4326"
                key = replacement.add_dataset(Dataset(entry["name"], entry["data"], crs=reference, source="legacy-display"))
                by_identity[id(entry["data"])] = key
            replacement.add_layer(Layer(key, entry["name"], entry.get("type", "geojson"),
                                        entry.get("data_field"), deepcopy(entry.get("folium_kwargs", {}))))
        with self.lock:
            self._datasets, self._layers, self._uploads = replacement._datasets, replacement._layers, replacement._uploads
            self._next_layer_number = replacement._next_layer_number
            self.task_journal.reset()
            self._results = []
            self._attribute_relations = []
            self._map_title = ""

    @classmethod
    def from_legacy(cls, raw=(), shown=()):
        session = cls()
        session.replace_from_legacy(raw, shown)
        return session

    def resolve_in(self, pool, reference, *, require_layer=False):
        numbered = re.fullmatch(r"\s*(?:图层|layer|ebene)\s*#?\s*(\d+)\s*", str(reference), re.I)
        if numbered:
            number = int(numbered.group(1))
            target = next((layer for layer in self._layers.values() if layer.display_number == number), None)
            if target is None:
                return None
            match = next((entry for entry in pool if entry.get("layer_id") == target.layer_id), None)
            if match:
                return match
            if require_layer:
                return None
            dataset = self._datasets[target.dataset_id]
            return {"name": target.name, "type": target.renderer, "data": self._payload(dataset),
                    "dataset_id": dataset.dataset_id, "layer_id": target.layer_id,
                    "layer_number": target.display_number, "crs": dataset.crs}
        layer_matches = [e for e in pool if e.get("layer_id") == reference]
        if layer_matches:
            return layer_matches[0]
        if not require_layer:
            # Explicit dataset identity is independent of display visibility.
            # Name lookup below retains the legacy tool's pool selection.
            try:
                dataset = self.get_dataset(reference)
            except KeyError:
                pass
            else:
                return {"name": dataset.name, "type": dataset.metadata.get("type", "geojson"),
                        "data": self._payload(dataset), "dataset_id": dataset.dataset_id, "crs": dataset.crs}
        matches = [e for e in pool if e.get("dataset_id") == reference]
        if matches and not require_layer:
            # Multiple renderings still refer to one dataset. Prefer its raw
            # projection when available; data operations do not choose a style.
            return next((e for e in matches if not e.get("layer_id")), matches[0])
        if not matches:
            matches = [e for e in pool if e["name"].lower() == reference.lower()]
        # A dataset plus its only same-named rendering denotes the same data;
        # multiple distinct renderings or same-named datasets are ambiguous.
        visible = [e for e in matches if e.get("layer_id")]
        if len(visible) == 1 and all(e["dataset_id"] == visible[0]["dataset_id"] for e in matches):
            return visible[0]
        unique = {(e.get("layer_id") or e.get("dataset_id")): e for e in matches}
        if len(unique) > 1:
            raise AmbiguousReference(f"Layer '{reference}' ist mehrdeutig. Bitte Dataset-ID oder Layer-ID verwenden.")
        return next(iter(unique.values()), None)

    def resolve(self, reference):
        with self.lock:
            raw, shown = self.legacy_layers()
            if reference in self._datasets:
                return self.get_dataset(reference), None
            match = self.resolve_in(shown + raw, reference)
            if match is None:
                raise KeyError(reference)
            return self.get_dataset(match["dataset_id"]), (
                self.get_layer(match["layer_id"]) if match.get("layer_id") else None)

    def record_failure(self, message, category="tool_error"):
        result = ToolResult(False, message, error_category=category)
        returned_result = deepcopy(result)
        with self.lock:
            self._results.append(result)
        return returned_result

    def commit_map_action(self, action, *, message, tool_name):
        """Apply a map-control command without rebuilding or validating data layers."""
        with self.lock:
            layers = dict(self._layers)
            action_type = action.get("type")
            if action_type == "remove":
                layer_id = action.get("layer_id")
                if layer_id not in layers:
                    raise KeyError("Unknown layer reference")
                del layers[layer_id]
            elif action_type == "visibility":
                layer_id = action.get("layer_id")
                if layer_id not in layers:
                    raise KeyError("Unknown layer reference")
                layers[layer_id] = replace(layers[layer_id], visible=bool(action.get("visible")))
                map_title = self._map_title
            elif action_type == "title":
                map_title = action.get("title")
                if not isinstance(map_title, str) or not map_title.strip():
                    raise ValueError("Map title must be a non-empty string")
            elif action_type == "clear_title":
                map_title = ""
            else:
                raise ValueError("Invalid map action")
            if action_type in {"remove", "visibility"}:
                map_title = self._map_title
            result = ToolResult(True, message)
            layers, result = deepcopy(self._numbered_layers(layers)), deepcopy(result)
            returned_result = deepcopy(result)
            self._layers = layers
            self._next_layer_number = len(layers) + 1
            self._map_title = map_title
            self._results.append(result)
            return returned_result

    def commit_result_dataset(self, dataset, *, message, metrics=None):
        """Publish a reusable statistical result without creating a map layer."""
        with self.lock:
            if dataset.dataset_id in self._datasets or any(key not in self._datasets for key in dataset.parent_ids):
                raise ValueError("Invalid result dataset identity or lineage")
            dataset = replace(dataset, crs=validate_dataset_crs(dataset))
            dataset = self._copy_dataset(dataset)
            self._payload(dataset)  # Check serialization before publication.
            result = ToolResult(True, message, (dataset.dataset_id,), metrics=deepcopy(metrics or {}))
            returned = deepcopy(result)
            self._datasets = {**self._datasets, dataset.dataset_id: dataset}
            self._results.append(result)
            return returned

    def commit_views(self, raw, shown, *, message, tool_name, parent_ids=(), metrics=None):
        """Validate detached tool output, then publish once under the session lock."""
        with self.lock:
            datasets, layers = dict(self._datasets), dict(self._layers)
            payload_ids = {id(e["data"]): e["dataset_id"] for e in raw + shown
                           if e.get("dataset_id") in datasets}
            created_data, created_layers, updated = [], [], []
            next_number = self._next_layer_number
            for entry in shown:
                if not isinstance(entry.get("name"), str) or not isinstance(entry.get("type"), str):
                    raise ValueError("Invalid layer contract")
                key = entry.get("dataset_id") or payload_ids.get(id(entry["data"]))
                if key is None:
                    data = entry["data"]
                    # Serialization validates transport compatibility, not GIS correctness.
                    if hasattr(data, "to_json") and hasattr(data, "geometry"):
                        json.loads(data.to_json())
                        crs = entry["crs"] if "crs" in entry else (str(data.crs) if data.crs else None)
                    else:
                        json.dumps(data)
                        crs = entry["crs"] if "crs" in entry else data_crs(data)
                        if crs is UNSPECIFIED_CRS:
                            crs = "EPSG:4326"
                    d = Dataset(entry["name"], deepcopy(data), crs, f"tool:{tool_name}", tuple(parent_ids),
                                {"format": entry.get("_format", "collection")})
                    d = replace(d, crs=validate_dataset_crs(d))
                    key = d.dataset_id
                    datasets[key] = d
                    payload_ids[id(data)] = key
                    created_data.append(key)
                if key not in datasets:
                    raise ValueError("Unknown dataset reference")
                if entry.get("layer_id") and entry["layer_id"] not in self._layers:
                    raise ValueError("Unknown layer reference")
                layer_id = entry.get("layer_id") or new_id()
                if layer_id in self._layers and self._layers[layer_id].dataset_id != key:
                    raise ValueError("Existing layer cannot change dataset reference")
                visible = layers[layer_id].visible if layer_id in layers else entry.get("visible", True)
                number = layers[layer_id].display_number if layer_id in layers else next_number
                if layer_id not in layers:
                    next_number += 1
                candidate = Layer(key, entry["name"], entry["type"], entry.get("data_field"),
                                  deepcopy(entry.get("folium_kwargs", {})), layer_id, visible, number)
                if layer_id not in layers:
                    created_layers.append(layer_id)
                elif candidate != layers[layer_id]:
                    updated.append(layer_id)
                layers[layer_id] = candidate
            for d in datasets.values():
                if any(key not in datasets for key in d.parent_ids):
                    raise ValueError("Invalid derived lineage")
            result = ToolResult(True, message, tuple(created_data), tuple(created_layers), tuple(updated), metrics or {})
            # All potentially failing work (including defensive copy) precedes publication.
            datasets = {key: self._copy_dataset(d) for key, d in datasets.items()}
            layers, result = deepcopy(self._numbered_layers(layers)), deepcopy(result)
            returned_result = deepcopy(result)
            self._datasets, self._layers = datasets, layers
            self._next_layer_number = len(layers) + 1
            self._results.append(result)
            return returned_result

classdef MockRavenProject < handle
    properties
        room_material_names
        shoebox_dims
        face_points
        face_definitions
        face_materials
        set_faces_calls
        material_calls
        source_names
        source_positions
        source_view_vectors
        source_up_vectors
        directivity_calls
        receiver_hrtf
        receiver_positions
        receiver_view_vectors
        room_material_names_override
    end

    methods
        function obj = MockRavenProject(room_material_names)
            if nargin < 1
                room_material_names = {};
                obj.room_material_names_override = [];
            else
                obj.room_material_names_override = room_material_names;
            end

            obj.room_material_names = room_material_names;
            obj.shoebox_dims = [];
            obj.face_points = [];
            obj.face_definitions = {};
            obj.face_materials = {};
            obj.set_faces_calls = 0;
            obj.material_calls = struct('slot_name', {}, 'absorp', {}, 'scatter', {});
            obj.source_names = {};
            obj.source_positions = [];
            obj.source_view_vectors = [];
            obj.source_up_vectors = [];
            obj.directivity_calls = {};
            obj.receiver_hrtf = '';
            obj.receiver_positions = [];
            obj.receiver_view_vectors = [];
        end

        function setModelToShoebox(obj, x, y, z)
            obj.shoebox_dims = [x y z];
        end

        function output_path = setModelToFaces(obj, points, faces, materials)
            obj.face_points = points;
            obj.face_definitions = faces;
            obj.face_materials = materials;
            obj.set_faces_calls = obj.set_faces_calls + 1;
            if isempty(obj.room_material_names_override)
                material_indices = cellfun(@(face) face(1), faces);
                obj.room_material_names = materials(material_indices);
            else
                obj.room_material_names = obj.room_material_names_override;
            end
            output_path = 'mock-room.ac';
        end

        function names = getRoomMaterialNames(obj)
            names = obj.room_material_names;
        end

        function setMaterial(obj, slot_name, absorp, scatter)
            obj.material_calls(end + 1) = struct( ...
                'slot_name', slot_name, ...
                'absorp', absorp, ...
                'scatter', scatter);
        end

        function setSourceNames(obj, source_names)
            obj.source_names = source_names;
        end

        function setSourcePositions(obj, source_positions)
            obj.source_positions = source_positions;
        end

        function setSourceViewVectors(obj, source_view_vectors)
            obj.source_view_vectors = source_view_vectors;
        end

        function setSourceUpVectors(obj, source_up_vectors)
            obj.source_up_vectors = source_up_vectors;
        end

        function setSourceDirectivity(obj, directivity_path)
            obj.directivity_calls{end + 1} = char(string(directivity_path));
        end

        function setReceiverHRTF(obj, hrtf_path)
            obj.receiver_hrtf = char(string(hrtf_path));
        end

        function setReceiverPositions(obj, receiver_positions)
            obj.receiver_positions = receiver_positions;
        end

        function setReceiverViewVectors(obj, receiver_view_vectors)
            obj.receiver_view_vectors = receiver_view_vectors;
        end
    end
end

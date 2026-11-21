# Tests generated per-VM provisioning shell without running Vagrant/VirtualBox.
require "open3"

class FakeVM
  attr_accessor :box, :box_version, :hostname
  attr_reader :roles, :networks, :provisioners
  def initialize
    @roles = []
    @networks = []
    @provisioners = {}
  end
  def vm
    self
  end
  def synced_folder(*)
  end
  def define(role)
    @roles << role
    vm = FakeVM.new
    yield vm
    @networks.concat(vm.networks.map { |n| [role, n] })
    @provisioners[role] = vm.provisioners.values.first
  end
  def network(type, **options)
    raise "unexpected non-private NIC" unless type == "private_network"
    raise "missing VirtualBox internal network" unless options[:virtualbox__intnet]
    @networks << options
  end
  def provider(type)
    raise "unexpected hypervisor" unless type == "virtualbox"
    yield Struct.new(:memory, :cpus).new
  end
  def provision(type, inline:)
    raise "unexpected provisioner" unless type == "shell"
    @provisioners["src"] = inline  # extracted for a role-specific VM by define()
  end
end

module Vagrant
  def self.configure(version)
    raise "unsupported version" unless version == "2"
    mock = FakeVM.new
    yield mock
    raise "missing independent machines" unless mock.roles == %w[src router dst]
    raise "wrong number of isolated NICs" unless mock.networks.size == 4
    mock.provisioners.each do |role, script|
      raise "missing provisioning script for #{role}" unless script
      stdout, stderr, status = Open3.capture3("bash", "-n", stdin_data: script)
      raise "#{role}: invalid generated shell: #{stderr}" unless status.success?
      raise "#{role}: identity marker missing" unless script.include?('"role":"' + role + '"')
      raise "#{role}: raw heredoc not closed" unless role != "dst" ||
          script.match?(/^SERVICE$/)
    end
  end
end

load File.expand_path("../environments/vm/Vagrantfile", __dir__)
puts "PASS Vagrantfile: three guest scripts parsed, 4 internal NICs"
